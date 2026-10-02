# -*- coding: utf-8 -*-
"""游戏会议协议小测：昵称冲突 + 历史合成（无 Qt）。"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.gamemeet_net import (  # noqa: E402
    MeetClient,
    MeetHost,
    pick_unique_nickname,
    recent_debug_events,
    _nick_key,
    history_preview,
)


class TestNickCollision(unittest.TestCase):
    def test_pick_unique_casefold(self):
        final, renamed = pick_unique_nickname("Hu", ["hu"])
        self.assertTrue(renamed)
        self.assertNotEqual(_nick_key(final), "hu")

    def test_pick_unique_includes_host(self):
        final, renamed = pick_unique_nickname("Hu", ["Hu", "alice"])
        self.assertTrue(renamed)
        self.assertNotEqual(final, "Hu")

    def test_live_join_renames_against_host(self):
        host = MeetHost(display_name="Hu")
        ip, port = host.start(0)
        box = {}

        def on_welcome(uid, rev, text, name="", renamed=False):
            box["name"] = name
            box["renamed"] = renamed

        assigns = []

        def on_assign(n, r):
            assigns.append((n, r))

        client = MeetClient(
            display_name="Hu",
            on_welcome=on_welcome,
            on_nick_assign=on_assign,
        )
        try:
            client.connect(ip, port)
            deadline = time.time() + 3
            while time.time() < deadline and "name" not in box:
                time.sleep(0.05)
            self.assertIn("name", box)
            self.assertTrue(box["renamed"])
            self.assertNotEqual(_nick_key(box["name"]), "hu")
            self.assertEqual(_nick_key(client.display_name), _nick_key(box["name"]))
            roster_names = [u["name"] for u in host._roster_list()]
            self.assertIn("Hu", roster_names)
            self.assertIn(box["name"], roster_names)
            deadline = time.time() + 1
            while time.time() < deadline and not assigns:
                time.sleep(0.05)
            self.assertTrue(assigns, "expected nick_assign after collision")
            ev = [e for e in recent_debug_events(30) if e.get("event") == "nick_collision"]
            self.assertTrue(ev)
        finally:
            client.disconnect()
            host.stop()

    def test_two_clients_same_nick_second_renamed(self):
        """两名客户端同昵称：第二个必须被改名。"""
        host = MeetHost(display_name="HostOnly")
        ip, port = host.start(0)
        boxes = [{}, {}]

        def make_welcome(idx):
            def on_welcome(uid, rev, text, name="", renamed=False):
                boxes[idx]["name"] = name
                boxes[idx]["renamed"] = renamed
                boxes[idx]["uid"] = uid
            return on_welcome

        c1 = MeetClient(display_name="Twin", on_welcome=make_welcome(0))
        c2 = MeetClient(display_name="Twin", on_welcome=make_welcome(1))
        try:
            c1.connect(ip, port)
            deadline = time.time() + 3
            while time.time() < deadline and "name" not in boxes[0]:
                time.sleep(0.05)
            self.assertIn("name", boxes[0])
            self.assertFalse(boxes[0].get("renamed"))
            self.assertEqual(boxes[0]["name"], "Twin")

            c2.connect(ip, port)
            deadline = time.time() + 3
            while time.time() < deadline and "name" not in boxes[1]:
                time.sleep(0.05)
            self.assertIn("name", boxes[1])
            self.assertTrue(boxes[1]["renamed"])
            self.assertNotEqual(_nick_key(boxes[1]["name"]), "twin")
            roster = [u["name"] for u in host._roster_list()]
            self.assertIn("Twin", roster)
            self.assertIn(boxes[1]["name"], roster)
            keys = [_nick_key(n) for n in roster]
            self.assertEqual(len(keys), len(set(keys)), "roster nick keys must be unique")
        finally:
            c2.disconnect()
            c1.disconnect()
            host.stop()


class TestHistorySynthesize(unittest.TestCase):
    def test_join_gets_history_card_for_existing_doc(self):
        host = MeetHost(display_name="HostA")
        host.seed_doc("shared body text for card")
        self.assertEqual(host.current_history(), [])
        ip, port = host.start(0)
        got = {"hist": None, "text": None, "welcome_hist": None}

        def on_hist(items):
            if items:
                got["hist"] = list(items)

        def on_welcome(uid, rev, text, name="", renamed=False):
            got["text"] = text

        client = MeetClient(
            display_name="Joiner",
            on_welcome=on_welcome,
            on_history=on_hist,
        )
        try:
            client.connect(ip, port)
            deadline = time.time() + 3
            while time.time() < deadline and not got["hist"]:
                time.sleep(0.05)
            self.assertEqual(got["text"], "shared body text for card")
            self.assertTrue(got["hist"], "joiner must receive >=1 history card")
            self.assertEqual(got["hist"][0]["text"], "shared body text for card")
            self.assertTrue(host.current_history())
        finally:
            client.disconnect()
            host.stop()

    def test_prefilled_history_different_current_adds_card(self):
        """主机已有旧历史 + 当前正文不同 → ensure 补当前卡 → welcome.history 含当前正文。"""
        host = MeetHost(display_name="HostB")
        with host._lock:
            host._history = [{
                "id": "h-old001",
                "text": "old session leftover",
                "preview": history_preview("old session leftover"),
                "ts": time.time() - 100,
            }]
        host.seed_doc("CURRENT live body")
        # 旧逻辑会因 _history 非空直接 return False，漏掉当前正文卡
        self.assertTrue(host.ensure_doc_history())
        hist = host.current_history()
        self.assertGreaterEqual(len(hist), 2)
        self.assertEqual(hist[0]["text"], "CURRENT live body")

        ip, port = host.start(0)
        got = {"hist": None, "text": None}

        def on_hist(items):
            if items:
                got["hist"] = list(items)

        def on_welcome(uid, rev, text, name="", renamed=False):
            got["text"] = text

        client = MeetClient(
            display_name="Joiner2",
            on_welcome=on_welcome,
            on_history=on_hist,
        )
        try:
            client.connect(ip, port)
            deadline = time.time() + 3
            while time.time() < deadline and not got["hist"]:
                time.sleep(0.05)
            self.assertEqual(got["text"], "CURRENT live body")
            self.assertTrue(got["hist"])
            texts = [x["text"] for x in got["hist"]]
            self.assertIn("CURRENT live body", texts)
            self.assertEqual(got["hist"][0]["text"], "CURRENT live body")
            # welcome 内嵌 history：客户端本地也应已有
            client_hist = client.current_history()
            self.assertTrue(client_hist)
            self.assertIn("CURRENT live body", [x["text"] for x in client_hist])
        finally:
            client.disconnect()
            host.stop()

    def test_ensure_doc_history_idempotent(self):
        host = MeetHost(display_name="H")
        host.seed_doc("abc")
        self.assertTrue(host.ensure_doc_history())
        self.assertFalse(host.ensure_doc_history())
        self.assertEqual(len(host.current_history()), 1)

    def test_paste_no_duplicate_after_synthesize(self):
        host = MeetHost(display_name="H")
        host.seed_doc("old")
        host.ensure_doc_history()
        host.replace_doc_from_paste("new", prior_text="old")
        texts = [x["text"] for x in host.current_history()]
        self.assertEqual(texts.count("old"), 1)

    def test_client_synthesize_empty_history_nonempty_doc(self):
        """客户端侧：空历史 + 非空 welcome 正文 → 模拟 UI 自愈合成逻辑。"""
        # 无 Qt：复刻 page._ensure_client_history_for_doc 的核心判断
        role = "client"
        doc_text = "welcome synced body"
        history_items = []

        def ensure_client_history_for_doc():
            if role != "client":
                return False
            text = doc_text if doc_text is not None else ""
            if not str(text).strip():
                return False
            for it in history_items:
                if isinstance(it, dict) and str(it.get("text") or "") == text:
                    return False
            import uuid as _uuid
            entry = {
                "id": "h-" + _uuid.uuid4().hex[:10],
                "text": text,
                "preview": history_preview(text),
                "ts": time.time(),
            }
            history_items[:] = [entry] + list(history_items)
            return True

        self.assertTrue(ensure_client_history_for_doc())
        self.assertEqual(len(history_items), 1)
        self.assertEqual(history_items[0]["text"], "welcome synced body")
        self.assertFalse(ensure_client_history_for_doc())  # idempotent


if __name__ == "__main__":
    unittest.main()
