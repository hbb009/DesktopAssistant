# -*- coding: utf-8 -*-
"""听话模式意图分流：不启动 Qt / Ollama。"""
from __future__ import annotations

import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import os
import tempfile

from utils.ollama_chunk import _TASK_RE as TASK_RE  # noqa: E402
from utils import ollama_auto  # noqa: E402
from utils.ollama_auto import (  # noqa: E402
    catalog_chapter_count,
    catalog_finish_line,
    chapter_save_plan,
    is_convert_to_txt,
    is_save_original,
    pick_chapter_links,
    save_catalog_chapters,
)
from utils.ollama_dirs import export_txt  # noqa: E402
from utils.ollama_intent import classify  # noqa: E402


class TestClassify(unittest.TestCase):
    def test_chat_ignores_open_switches(self):
        r = classify("你好", web=True, dirs=True, obedient=True)
        self.assertEqual(r.name, "chat")
        self.assertEqual(r.tools, ())
        self.assertFalse(r.think)
        self.assertEqual(r.temperature, 0.4)

    def test_web_search(self):
        r = classify("搜索今天新闻", web=True, dirs=True, obedient=True)
        self.assertEqual(r.name, "agent")
        self.assertIn("web_search", r.tools)
        self.assertIn("web_fetch", r.tools)

    def test_write_diary_not_web(self):
        r = classify("今天写一篇日记并保存", web=True, dirs=True, obedient=True)
        self.assertEqual(r.name, "agent")
        self.assertEqual(r.tools, ("save_article",))
        self.assertFalse(r.uses_web())

    def test_rewrite_named_file(self):
        r = classify("把春日.md翻译成英文", web=False, dirs=True, obedient=True)
        self.assertEqual(r.name, "agent")
        self.assertIn("read_article", r.tools)
        self.assertIn("save_article", r.tools)

    def test_list_dir(self):
        r = classify("列出工作目录", web=True, dirs=True, obedient=True)
        self.assertEqual(r.name, "agent")
        self.assertEqual(r.tools, ("list_articles", "read_article", "save_notes", "read_notes"))

    def test_web_then_write(self):
        r = classify("搜索今天新闻写一篇并保存", web=True, dirs=True, obedient=True)
        self.assertEqual(r.name, "agent")
        self.assertTrue(r.uses_web())
        self.assertIn("save_article", r.tools)

    def test_legacy_keeps_all_tools(self):
        r = classify("你好", web=True, dirs=True, obedient=False)
        self.assertEqual(r.name, "agent")
        self.assertEqual(
            r.tools,
            ("web_search", "web_fetch", "list_articles", "read_article", "save_article", "save_notes", "read_notes"),
        )

    def test_no_dirs_write_stays_chat(self):
        r = classify("写一篇春日随笔并保存", web=False, dirs=False, obedient=True)
        self.assertEqual(r.name, "chat")
        self.assertEqual(r.tools, ())

    def test_web_off_search_stays_chat(self):
        r = classify("搜索今天新闻", web=False, dirs=False, obedient=True)
        self.assertEqual(r.name, "chat")

    def test_url_what_do_you_see(self):
        r = classify(
            "https://www.alicesw.com/other/chapters/id/53120.html 你看到了什么",
            web=True, dirs=True, obedient=True,
        )
        self.assertEqual(r.name, "agent")
        self.assertIn("web_fetch", r.tools)
        self.assertNotIn("save_article", r.tools)

    def test_url_enter_chapters_then_save(self):
        text = (
            "https://www.alicesw.com/other/chapters/id/53120.html，"
            "这个网站是一套小说的目录，请分别进入4个章节中，帮我将内容保存下来"
        )
        self.assertFalse(is_save_original(text, TASK_RE))
        r = classify(text, web=True, dirs=True, obedient=True)
        self.assertEqual(r.name, "agent")
        self.assertIn("web_fetch", r.tools)
        self.assertIn("save_article", r.tools)

    def test_url_save_this_page(self):
        text = "https://example.com/a.html 把这个网页原文保存到工作目录"
        self.assertTrue(is_save_original(text, TASK_RE))

    def test_convert_txt_is_not_a_rewrite(self):
        text = "把工作目录中的文件转为txt格式"
        self.assertTrue(is_convert_to_txt(text))
        self.assertTrue(is_convert_to_txt("把春日.md转成txt"))
        self.assertFalse(is_convert_to_txt("把春日.md总结后转成txt"))
        self.assertFalse(is_convert_to_txt("把春日.md翻译成英文"))

    def test_export_txt_copies_markdown(self):
        body = "第一段。\n第二段。\n"
        other = "另一篇。\n"
        with tempfile.TemporaryDirectory() as folder:
            src = os.path.join(folder, "春日.md")
            with open(src, "w", encoding="utf-8", newline="\n") as f:
                f.write(body)
            with open(os.path.join(folder, "秋日.md"), "w", encoding="utf-8", newline="\n") as f:
                f.write(other)
            note = export_txt(folder, "把工作目录中的文件转为txt格式")
            with open(os.path.join(folder, "春日.txt"), encoding="utf-8") as f:
                got = f.read()
            with open(os.path.join(folder, "秋日.txt"), encoding="utf-8") as f:
                got2 = f.read()
            with open(src, encoding="utf-8") as f:
                still = f.read()
            one = export_txt(folder, "只把《春日》转成txt")
        self.assertEqual(got, body)
        self.assertEqual(got2, other)
        self.assertEqual(still, body)
        self.assertIn("春日.md → 春日.txt", note)
        self.assertIn("秋日.md → 秋日.txt", note)
        self.assertIn("已有相同内容", one)
        self.assertNotIn("秋日.md →", one)


_CATALOG = "https://ex.com/other/chapters/id/1.html"


def _ask(n):
    return (
        f"{_CATALOG}，这个网站是一套小说的目录，"
        f"请分别进入{n}个章节中，帮我将内容保存下来"
    )


def _catalog_links():
    nav = [(f"分类{i}", f"https://ex.com/lists/{i}.html") for i in range(34)]
    other = [
        ("本日排行", "https://ex.com/other/rank.html"),
        ("书名", "https://ex.com/novel/1.html"),
        ("别的书", "https://ex.com/book/8/z.html"),
    ]
    book = [
        ("01", "https://ex.com/book/9/a.html"),
        ("02-06", "https://ex.com/book/9/b.html"),
        ("07-08", "https://ex.com/book/9/c.html"),
        ("09-10", "https://ex.com/book/9/d.html"),
        ("11-14", "https://ex.com/book/9/e.html"),
        ("15-16", "https://ex.com/book/9/f.html"),
        ("前传", "https://ex.com/book/9/g.html"),
    ]
    return nav + other + book


class TestCatalogSave(unittest.TestCase):
    def test_count_follows_the_user_sentence(self):
        self.assertEqual(catalog_chapter_count(_ask(7)), 7)
        self.assertEqual(catalog_chapter_count(_ask(4)), 4)
        self.assertEqual(catalog_chapter_count(_ask(255)), 255)
        self.assertEqual(
            catalog_chapter_count("https://ex.com/a.html 你看到了什么"),
            0,
        )
        self.assertEqual(
            catalog_chapter_count("https://ex.com/a.html 把这个网页原文保存到工作目录"),
            0,
        )
        self.assertEqual(
            catalog_chapter_count(_ask(7).replace("保存", "总结并保存")),
            0,
        )
        self.assertEqual(catalog_chapter_count("把工作目录中的文件转为txt格式"), 0)

    def test_loose_chapter_sentence_is_a_program_plan(self):
        loose = f"{_CATALOG}，这个网站是一套小说的目录，请把章节都保存下来"
        self.assertEqual(chapter_save_plan(loose), -1)
        self.assertEqual(chapter_save_plan(_ask(7)), 7)
        self.assertEqual(chapter_save_plan(_ask(255)), 255)
        self.assertEqual(chapter_save_plan("写三个章节并保存到工作目录"), 0)
        self.assertEqual(chapter_save_plan("把工作目录中的文件转为txt格式"), 0)
        self.assertEqual(
            chapter_save_plan("https://ex.com/a.html 把这个网页原文保存到工作目录"),
            0,
        )

    def test_pick_keeps_one_book_in_page_order(self):
        got = pick_chapter_links(_catalog_links(), _CATALOG)
        self.assertEqual(
            [name for name, _href in got],
            ["01", "02-06", "07-08", "09-10", "11-14", "15-16", "前传"],
        )
        cut = _catalog_links()[:40]
        self.assertEqual(
            [name for name, _href in pick_chapter_links(cut, _CATALOG)],
            ["01", "02-06", "07-08"],
        )

    def test_save_writes_each_chapter_without_a_model(self):
        bodies = {
            "https://ex.com/book/9/a.html": "甲" * 220,
            "https://ex.com/book/9/b.html": "乙" * 220,
            "https://ex.com/book/9/c.html": "丙" * 220,
            "https://ex.com/book/9/d.html": "丁" * 220,
            "https://ex.com/book/9/e.html": "戊" * 220,
            "https://ex.com/book/9/f.html": "己" * 220,
            "https://ex.com/book/9/g.html": "庚" * 30,
        }
        seen = []

        def fake(url, should_stop=None, on_status=None, max_pages=30, link_limit=40):
            seen.append((url, link_limit))
            if url == _CATALOG:
                return {"title": "目录", "text": "目录\n", "url": url, "links": _catalog_links()}
            return {"title": "章", "text": bodies.get(url, ""), "url": url, "links": []}

        with tempfile.TemporaryDirectory() as folder:
            kept = os.path.join(folder, "笔记.txt")
            with open(kept, "w", encoding="utf-8", newline="\n") as f:
                f.write("不要动。\n")
            same = os.path.join(folder, "01.txt")
            with open(same, "w", encoding="utf-8", newline="\n") as f:
                f.write("甲" * 220 + "\n")
            with unittest.mock.patch("utils.ollama_auto.fetch_page", fake):
                note = save_catalog_chapters(folder, _ask(7))
            with open(os.path.join(folder, "02-06.txt"), encoding="utf-8") as f:
                chapter = f.read()
            with open(kept, encoding="utf-8") as f:
                still = f.read()
            names = set(os.listdir(folder))
        self.assertEqual(seen[0], (_CATALOG, 0))
        self.assertEqual(chapter, "乙" * 220 + "\n")
        self.assertEqual(still, "不要动。\n")
        self.assertIn("01.txt", names)
        self.assertIn("02-06.txt", names)
        self.assertIn("15-16.txt", names)
        self.assertNotIn("前传.txt", names)
        self.assertNotIn("目录任务临时文件.txt", names)
        self.assertIn("正文太短", note)
        self.assertIn("没有再写", note)
        self.assertTrue(note.startswith("未完成：缺 前传。"))
        self.assertNotIn("乙" * 20, note)

    def test_same_body_under_another_name_counts(self):
        def fake(url, should_stop=None, on_status=None, max_pages=30, link_limit=40):
            if url == _CATALOG:
                return {"title": "目录", "text": "目录\n", "url": url, "links": _catalog_links()}
            if url.endswith("/b.html"):
                return {"title": "章", "text": "乙" * 220, "url": url, "links": []}
            return {"title": "章", "text": "甲" * 220, "url": url, "links": []}

        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "02.txt"), "w", encoding="utf-8", newline="\n") as f:
                f.write("乙" * 220 + "\n")
            with unittest.mock.patch("utils.ollama_auto.fetch_page", fake):
                note = save_catalog_chapters(folder, _ask(2))
            names = set(os.listdir(folder))
        self.assertTrue(note.startswith("已完成，2 章都在。"))
        self.assertIn("已在 02.txt", note)
        self.assertIn("02.txt", names)
        self.assertNotIn("02-06.txt", names)
        self.assertIn("01.txt", names)

    def test_finish_line_compares_request_and_disk(self):
        self.assertEqual(catalog_finish_line(7, 7, [], 7, False), "已完成，7 章都在。")
        self.assertEqual(catalog_finish_line(7, 7, ["前传"], 6, False), "未完成：缺 前传。")
        self.assertEqual(
            catalog_finish_line(7, 1, [], 1, False),
            "未完成：目录里只有 1 个章节链接，要求 7 章。",
        )
        self.assertEqual(catalog_finish_line(7, 7, ["09-10"], 2, True), "未完成：已停止，盘上 2 章。")

    def test_past_old_cap_saves_each_link_then_drops_the_body(self):
        n = 45
        links = [
            (f"{i:03d}", f"https://ex.com/book/9/c{i}.html") for i in range(1, n + 1)
        ]

        def fake(url, should_stop=None, on_status=None, max_pages=30, link_limit=40):
            if url == _CATALOG:
                self.assertEqual(link_limit, 0)
                return {"title": "目录", "text": "目录\n", "url": url, "links": links}
            return {"title": "章", "text": url + ("甲" * 220), "url": url, "links": []}

        with tempfile.TemporaryDirectory() as folder:
            with unittest.mock.patch("utils.ollama_auto.fetch_page", fake):
                note = save_catalog_chapters(folder, _ask(n))
            names = [
                name for name in os.listdir(folder)
                if name.endswith(".txt") and name != "目录任务临时文件.txt"
            ]
            task = os.path.join(folder, "目录任务临时文件.txt")
            with open(task, encoding="utf-8") as f:
                raw = f.read().splitlines()
            listed = [ln for ln in raw if "\thttp" in ln]
            progress = raw[0].split("\t", 1)[1].strip()
        self.assertEqual(len(names), n)
        self.assertTrue(note.startswith(f"已完成，{n} 章都在。"))
        self.assertNotIn("甲" * 20, note)
        self.assertEqual(len(listed), n)
        self.assertEqual(progress, str(n))
        self.assertIn("任务记在", note)

    def test_stopped_long_task_keeps_the_link_file(self):
        n = 25
        links = [(f"{i:03d}", f"https://ex.com/book/9/c{i}.html") for i in range(1, n + 1)]
        state = {"chapters": 0}

        def stop():
            return state["chapters"] >= 3

        def fake(url, should_stop=None, on_status=None, max_pages=30, link_limit=40):
            if url != _CATALOG:
                state["chapters"] += 1
            if url == _CATALOG:
                return {"title": "目录", "text": "目录\n", "url": url, "links": links}
            return {"title": "章", "text": url + ("甲" * 220), "url": url, "links": []}

        with tempfile.TemporaryDirectory() as folder:
            with unittest.mock.patch("utils.ollama_auto.fetch_page", fake):
                note = save_catalog_chapters(folder, _ask(n), should_stop=stop)
            saved = [
                name for name in os.listdir(folder)
                if name.endswith(".txt") and name != "目录任务临时文件.txt"
            ]
            with open(os.path.join(folder, "目录任务临时文件.txt"), encoding="utf-8") as f:
                raw = f.read().splitlines()
            listed = [ln for ln in raw if "\thttp" in ln]
            progress = raw[0].split("\t", 1)[1].strip()
        self.assertTrue(note.startswith("未完成：已停止，盘上 3 章。"))
        self.assertEqual(len(saved), 3)
        self.assertEqual(len(listed), n)
        self.assertEqual(progress, "3")

    def test_resume_reads_the_remaining_lines(self):
        n = 25
        links = [(f"{i:03d}", f"https://ex.com/book/9/c{i}.html") for i in range(1, n + 1)]
        seen = []

        def fake(url, should_stop=None, on_status=None, max_pages=30, link_limit=40):
            seen.append(url)
            if url == _CATALOG:
                return {"title": "目录", "text": "目录\n", "url": url, "links": links}
            return {"title": "章", "text": url + ("甲" * 220), "url": url, "links": []}

        with tempfile.TemporaryDirectory() as folder:
            task = os.path.join(folder, "目录任务临时文件.txt")
            with open(task, "w", encoding="utf-8", newline="\n") as f:
                f.write("进度\t22\n")
                f.write("".join(f"{name}\t{href}\n" for name, href in links))
            with unittest.mock.patch("utils.ollama_auto.fetch_page", fake):
                note = save_catalog_chapters(folder, _ask(n))
            saved = [
                name for name in os.listdir(folder)
                if name.endswith(".txt") and name != "目录任务临时文件.txt"
            ]
            with open(task, encoding="utf-8") as f:
                progress = f.read().splitlines()[0].split("\t", 1)[1].strip()
        self.assertIn("接着上次的文本任务", note)
        self.assertEqual(progress, str(n))
        self.assertEqual(sorted(saved), ["023.txt", "024.txt", "025.txt"])
        self.assertEqual(seen[0], _CATALOG)
        self.assertEqual(len(seen), 4)

    def test_loose_sentence_saves_every_chapter_link(self):
        text = f"{_CATALOG}，这个网站是一套小说的目录，请把章节都保存下来"

        def fake(url, should_stop=None, on_status=None, max_pages=30, link_limit=40):
            if url == _CATALOG:
                return {"title": "目录", "text": "目录\n", "url": url, "links": _catalog_links()}
            name = url.rsplit("/", 1)[-1]
            return {"title": name, "text": name * 220, "url": url, "links": []}

        with tempfile.TemporaryDirectory() as folder:
            with unittest.mock.patch("utils.ollama_auto.fetch_page", fake):
                note = save_catalog_chapters(folder, text)
            names = set(os.listdir(folder))
        self.assertTrue(note.startswith("已完成，7 章都在。"))
        self.assertIn("01.txt", names)
        self.assertIn("02-06.txt", names)
        self.assertIn("前传.txt", names)

    def test_retry_connection_reset_then_save(self):
        calls = {"n": 0}

        def fake(url, should_stop=None, on_status=None, max_pages=30, link_limit=40):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ollama_auto.PageError(
                    "打开网页失败：('Connection aborted.', ConnectionResetError(10054))"
                )
            if url == _CATALOG:
                return {
                    "title": "目录", "text": "目录\n", "url": url,
                    "links": [("01", "https://ex.com/book/9/a.html")],
                }
            return {"title": "01", "text": "甲" * 220, "url": url, "links": []}

        with tempfile.TemporaryDirectory() as folder:
            with unittest.mock.patch("utils.ollama_auto.fetch_page", fake), \
                    unittest.mock.patch("utils.ollama_auto.time.sleep", lambda _s: None):
                note = save_catalog_chapters(folder, _ask(4))
            with open(os.path.join(folder, "01.txt"), encoding="utf-8") as f:
                got = f.read()
        self.assertEqual(got, "甲" * 220 + "\n")
        self.assertIn("目录里只有 1 个章节链接", note)
        self.assertGreaterEqual(calls["n"], 2)


class TestShortPageBody(unittest.TestCase):
    def test_short_read_content_is_not_dropped_as_an_ad(self):
        from bs4 import BeautifulSoup

        html = """
        <html><body>
        <div id="j_setting" class="setting">
        目录 设置 手机 书架 书页 简体 加载中 阅读主题 雅黑 宋体 楷书 字体大小 页面宽度 阅读方式 保存 取消
        </div>
        <div class="sidebar-ad">推荐广告一句话</div>
        <div class="read-content j_readContent user_ad_content">
        <p>短章第一段，确认不足两百字的正文还在，不要被广告规则删掉。</p>
        <p>短章第二段，文件里应该是这两段，而不是旁边的设置栏。</p>
        </div>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        ollama_auto._clean_soup(soup)
        text = ollama_auto._block_text(ollama_auto._best_block(soup))
        self.assertIn("短章第一段", text)
        self.assertIn("短章第二段", text)
        self.assertNotIn("阅读主题", text)
        self.assertNotIn("推荐广告", text)

    def test_catalog_keeps_a_short_content_chapter(self):
        short = "甲" * 199

        def fake(url, should_stop=None, on_status=None, max_pages=30, link_limit=40):
            if "chapters" in url:
                return {
                    "title": "目录", "text": "目录\n", "url": url,
                    "links": [("前言", "https://ex.com/book/9/preface.html")],
                }
            return {
                "title": "前言", "text": short + "\n", "url": url,
                "links": [], "from_content": True,
            }

        ask = "https://ex.com/other/chapters/id/1.html，这个网站是一套小说的目录，请把章节都保存下来"
        with tempfile.TemporaryDirectory() as folder:
            with unittest.mock.patch("utils.ollama_auto.fetch_page", fake):
                note = save_catalog_chapters(folder, ask)
            with open(os.path.join(folder, "前言.txt"), encoding="utf-8") as f:
                got = f.read()
        self.assertEqual(got, short + "\n")
        self.assertTrue(note.startswith("已完成"))

    def test_long_chapter_titles_stay_on_the_catalog(self):
        from bs4 import BeautifulSoup

        titles = [f"第{i}章 " + ("甲" * 90) for i in range(1, 10)]
        links = []
        for i, title in enumerate(titles, 1):
            links.append(
                f'<a href="/book/39624/c{i}.html">{title}</a>'
            )
        links.append('<a href="/lists/4.html">' + ("乙" * 90) + "</a>")
        html = "<html><body>" + "".join(links) + "</body></html>"
        soup = BeautifulSoup(html, "html.parser")
        got = ollama_auto._page_links(
            soup, "https://www.alicesw.com/other/chapters/id/38107.html", limit=120,
        )
        picked = pick_chapter_links(got, "https://www.alicesw.com/other/chapters/id/38107.html")
        self.assertEqual(len(picked), 9)
        self.assertEqual([name[:3] for name, _href in picked], [f"第{i}章" for i in range(1, 10)])
        self.assertTrue(all("乙" not in name for name, _href in got))

    def test_uncapped_page_links_keep_a_long_catalog(self):
        from bs4 import BeautifulSoup

        links = [
            f'<a href="/book/9/c{i}.html">{i:03d}</a>' for i in range(1, 51)
        ]
        soup = BeautifulSoup("<html><body>" + "".join(links) + "</body></html>", "html.parser")
        got = ollama_auto._page_links(soup, _CATALOG, limit=0)
        self.assertEqual(len(got), 50)
        self.assertEqual(got[0][0], "001")
        self.assertEqual(got[-1][0], "050")


if __name__ == "__main__":
    unittest.main()
