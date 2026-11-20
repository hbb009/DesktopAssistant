# -*- coding: utf-8 -*-
"""FnCall 循环：模型调工具 → 程序执行 → 再交给模型，直到没有 tool_call。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.ollama_agent import (  # noqa: E402
    CATALOG_DONE_MARK,
    clear_fetched,
    pick_save_text,
    remember_fetch,
    run_fncall,
)


class TestFnCall(unittest.TestCase):
    def test_three_rounds_fetch_then_save_then_stop(self):
        steps = [
            ("", [{"type": "function", "function": {"name": "web_fetch", "arguments": {"url": "http://a"}}}]),
            ("", [{"type": "function", "function": {"name": "save_article", "arguments": {"filename": "a.txt"}}}]),
            ("四章都存好了", []),
        ]
        calls = []

        def round_iter(messages, tools, sink, think):
            spoken, tool_calls = steps.pop(0)
            sink["content"] = [spoken]
            sink["calls"] = tool_calls
            sink["text_calls"] = []
            sink["done_reason"] = "stop"
            if spoken:
                yield spoken

        def exec_tool(name, args, spoken=""):
            calls.append(name)
            if name == "save_article":
                return "已保存：D:/文章/a.txt（12 字）"
            return "正文和链接"

        out = "".join(run_fncall(
            round_iter=round_iter,
            exec_tool=exec_tool,
            messages=[{"role": "user", "content": "进四章并保存"}],
            tools=[{"type": "function", "function": {"name": "web_fetch"}}],
            max_calls=20,
        ))
        self.assertEqual(calls, ["web_fetch", "save_article"])
        self.assertIn("四章都存好了", out)
        self.assertFalse(steps)

    def test_save_uses_fetched_page_not_spoken(self):
        clear_fetched()
        remember_fetch(
            "https://ex.com/ch1",
            "第一章 这个世界打屁股都是往缝里打",
            "这是六千字的章节正文。" * 20,
        )
        body = pick_save_text(
            {"filename": "第一章 这个世界打屁股都是往缝里打.md"},
            spoken="正在保存第一章。",
        )
        self.assertGreater(len(body), 200)
        self.assertIn("章节正文", body)
        self.assertNotIn("正在保存", body)

    def test_do_not_reuse_last_page_for_another_name(self):
        clear_fetched()
        remember_fetch("https://ex.com/ch1", "第一章", "第一章正文。" * 30)
        remember_fetch("https://ex.com/ch2", "第二章", "第二章正文。" * 30)
        body = pick_save_text({"filename": "第七章.txt"}, spoken="")
        self.assertEqual(body, "")

    def test_refuse_short_spoken_without_fetch(self):
        clear_fetched()
        body = pick_save_text({"filename": "a.md"}, spoken="已保存。")
        self.assertEqual(body, "")

    def test_context_full_after_save_does_not_raise(self):
        def round_iter(messages, tools, sink, think):
            if any("已保存" in str(m.get("content") or "") for m in messages):
                raise RuntimeError("Ollama 返回错误（HTTP 500）：context length exceeded")
            sink["content"] = [""]
            sink["calls"] = [{
                "type": "function",
                "function": {"name": "save_article", "arguments": {"filename": "a.txt"}},
            }]
            sink["text_calls"] = []
            sink["done_reason"] = "stop"
            return
            yield ""

        def exec_tool(name, args, spoken=""):
            return "已保存：D:/文章/a.txt（12 字）"

        out = "".join(run_fncall(
            round_iter=round_iter,
            exec_tool=exec_tool,
            messages=[{"role": "user", "content": "保存"}],
            tools=[],
            max_calls=4,
        ))
        self.assertIn("上下文已满", out)
        self.assertIn("a.txt", out)

    def test_same_save_failure_stops(self):
        calls = []

        def round_iter(messages, tools, sink, think):
            sink["content"] = [""]
            sink["calls"] = [{
                "type": "function",
                "function": {"name": "save_article", "arguments": {"filename": "03.txt"}},
            }]
            sink["text_calls"] = []
            sink["done_reason"] = "stop"
            return
            yield ""

        def exec_tool(name, args, spoken=""):
            calls.append(args.get("filename"))
            return "没有保存：没有可用的网页正文"

        statuses = []
        out = "".join(run_fncall(
            round_iter=round_iter,
            exec_tool=exec_tool,
            messages=[{"role": "user", "content": "保存"}],
            tools=[],
            on_status=statuses.append,
            max_calls=6,
        ))
        self.assertEqual(calls, ["03.txt", "03.txt"])
        self.assertIn("未完成：卡在保存 03.txt", out)
        self.assertIn("连续失败两次", out)
        self.assertTrue(any(item.startswith("未完成：卡在保存 03.txt") for item in statuses))

    def test_different_failures_run_until_cap(self):
        step = {"n": 0}
        calls = []

        def round_iter(messages, tools, sink, think):
            step["n"] += 1
            sink["content"] = [""]
            sink["calls"] = [{
                "type": "function",
                "function": {"name": "save_article", "arguments": {"filename": f"0{step['n']}.txt"}},
            }]
            sink["text_calls"] = []
            sink["done_reason"] = "stop"
            return
            yield ""

        def exec_tool(name, args, spoken=""):
            calls.append(args.get("filename"))
            return "没有保存：没有可用的网页正文"

        statuses = []
        out = "".join(run_fncall(
            round_iter=round_iter,
            exec_tool=exec_tool,
            messages=[{"role": "user", "content": "保存"}],
            tools=[],
            on_status=statuses.append,
            max_calls=3,
        ))
        self.assertEqual(calls, ["01.txt", "02.txt", "03.txt"])
        self.assertIn("进度已记下", out)
        self.assertTrue(any("进度已记下" in item for item in statuses))

    def test_stop_review_rejects_an_early_finish_once(self):
        rounds = {"n": 0}

        def round_iter(messages, tools, sink, think):
            rounds["n"] += 1
            spoken = "先结束" if rounds["n"] == 1 else "第二次结束"
            sink["content"] = [spoken]
            sink["calls"] = []
            sink["text_calls"] = []
            sink["done_reason"] = "stop"
            yield spoken

        def review(spoken):
            if spoken.startswith("先结束"):
                return "章节还没存完"
            return ""

        out = "".join(run_fncall(
            round_iter=round_iter,
            exec_tool=lambda name, args, spoken="": "",
            messages=[{"role": "user", "content": "保存章节"}],
            tools=[],
            stop_review=review,
            max_calls=5,
        ))
        self.assertEqual(rounds["n"], 2)
        self.assertIn("先结束", out)
        self.assertIn("第二次结束", out)

    def test_catalog_executor_ends_the_loop(self):
        rounds = {"n": 0}

        def round_iter(messages, tools, sink, think):
            rounds["n"] += 1
            sink["content"] = [""]
            sink["calls"] = [{
                "type": "function",
                "function": {"name": "web_fetch", "arguments": {"url": "http://catalog"}},
            }]
            sink["text_calls"] = []
            sink["done_reason"] = "stop"
            return
            yield ""

        def exec_tool(name, args, spoken=""):
            return CATALOG_DONE_MARK + "\n已完成，2 章都在。\n已保存：D:/文章/01.txt（20 字）"

        statuses = []
        out = "".join(run_fncall(
            round_iter=round_iter,
            exec_tool=exec_tool,
            messages=[{"role": "user", "content": "保存章节"}],
            tools=[],
            on_status=statuses.append,
            max_calls=6,
        ))
        self.assertEqual(rounds["n"], 1)
        self.assertIn("已完成，2 章都在", out)
        self.assertTrue(any(item.startswith("已完成，2 章都在") for item in statuses))


if __name__ == "__main__":
    unittest.main()
