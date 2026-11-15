# -*- coding: utf-8 -*-
"""Nested save_article paths, overwrite, empty-content retry for skill-like files."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.ollama_agent import run_fncall  # noqa: E402
from utils.ollama_dirs import DirBudget, clear_partial_reads, run_dir_tool  # noqa: E402


NESTED = "generated-skills/novel-style-writer/references/style-guide.md"


class TestDirSave(unittest.TestCase):
    def setUp(self):
        clear_partial_reads()
        self.td = tempfile.TemporaryDirectory()
        self.root = self.td.name

    def tearDown(self):
        self.td.cleanup()

    def _save(self, filename, content="", reply="", mode="overwrite", budget=None):
        args = {"filename": filename, "mode": mode}
        if content != "":
            args["content"] = content
        return run_dir_tool(
            "save_article", args, article_dir=self.root,
            budget=budget or DirBudget(), reply_body=reply,
        )

    def test_nested_references_style_guide_creates_dirs(self):
        body = "# 风格指南\n\n" + ("这是一段足够长的说明文字。" * 20) + "\n"
        out = self._save(NESTED, body)
        dest = Path(self.root) / "generated-skills" / "novel-style-writer" / "references" / "style-guide.md"
        self.assertTrue(dest.is_file(), out)
        self.assertIn("已保存：", out)
        self.assertIn("style-guide.md", out)
        text = dest.read_text(encoding="utf-8")
        self.assertEqual(text, body)
        self.assertGreater(len(text), 200)

    def test_overwrite_existing_nested_file(self):
        first = self._save(NESTED, "第一版内容。" * 30)
        self.assertIn("已保存：", first)
        second = self._save(NESTED, "第二版已经覆盖。" * 30)
        self.assertIn("已保存：", second)
        dest = Path(self.root) / "generated-skills" / "novel-style-writer" / "references" / "style-guide.md"
        text = dest.read_text(encoding="utf-8")
        self.assertIn("第二版已经覆盖", text)
        self.assertNotIn("第一版内容", text)

    def test_empty_save_without_content_is_refused(self):
        out = self._save(NESTED, content="", reply="先保存风格指南。")
        self.assertTrue(out.startswith("没有保存：没有传入正文"), out)
        self.assertIn("content", out)
        dest = Path(self.root) / "generated-skills" / "novel-style-writer" / "references" / "style-guide.md"
        self.assertFalse(dest.exists())

    def test_web_url_empty_still_mentions_fetch(self):
        out = run_dir_tool(
            "save_article",
            {"filename": "第一章.md", "url": "https://ex.com/ch1"},
            article_dir=self.root, budget=DirBudget(), reply_body="正在保存。",
        )
        self.assertTrue(out.startswith("没有保存：没有可用的网页正文"), out)
        self.assertIn("web_fetch", out)


class TestEmptyContentRetry(unittest.TestCase):
    def _calls_of(self, filename, results, max_calls=8):
        remaining = list(results)
        seen = []
        hints = []

        def round_iter(messages, tools, sink, think):
            for m in messages:
                text = str(m.get("content") or "")
                if "没有正文" in text and "content" in text:
                    hints.append(text)
            if not remaining:
                sink["content"] = ["结束"]
                sink["calls"] = []
                sink["text_calls"] = []
                sink["done_reason"] = "stop"
                yield "结束"
                return
            sink["content"] = [""]
            sink["calls"] = [{
                "type": "function",
                "function": {"name": "save_article", "arguments": {"filename": filename}},
            }]
            sink["text_calls"] = []
            sink["done_reason"] = "stop"
            return
            yield ""

        def exec_tool(name, args, spoken=""):
            seen.append(args.get("filename"))
            if remaining:
                return remaining.pop(0)
            return "已保存：X（10 字）"

        statuses = []
        out = "".join(run_fncall(
            round_iter=round_iter,
            exec_tool=exec_tool,
            messages=[{"role": "user", "content": "整理成 Skill"}],
            tools=[],
            on_status=statuses.append,
            max_calls=max_calls,
        ))
        return seen, out, statuses, hints

    def test_self_written_empty_gets_hint_then_can_succeed(self):
        empty = "没有保存：没有传入正文，这一轮回复也太短，不能当文章。"
        ok = "已保存：D:/tmp/generated-skills/novel-style-writer/references/style-guide.md（800 字）"
        seen, out, statuses, hints = self._calls_of(
            "generated-skills/novel-style-writer/references/style-guide.md",
            [empty, empty, ok],
        )
        self.assertEqual(len(seen), 3)
        self.assertTrue(hints, "second failure should inject a content hint")
        self.assertIn("结束", out)
        self.assertNotIn("卡在保存", out)

    def test_self_written_empty_stops_after_hint_still_fails(self):
        empty = "没有保存：没有传入正文，这一轮回复也太短，不能当文章。"
        fn = "generated-skills/novel-style-writer/references/style-guide.md"
        seen, out, statuses, hints = self._calls_of(fn, [empty, empty, empty])
        self.assertEqual(len(seen), 3)
        self.assertTrue(hints)
        self.assertIn("未完成：卡在保存 " + fn, out)
        self.assertIn("连续失败两次", out)
        self.assertIn("原因：没有保存：没有传入正文", out)

    def test_transient_then_success(self):
        seen, out, statuses, hints = self._calls_of(
            "a.md",
            ["文章太长（200000 字），没有保存。", "已保存：D:/tmp/a.md（12 字）"],
        )
        self.assertEqual(seen, ["a.md", "a.md"])
        self.assertIn("结束", out)
        self.assertNotIn("卡在保存", out)


if __name__ == "__main__":
    unittest.main()