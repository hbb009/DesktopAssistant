# -*- coding: utf-8 -*-
"""Tiny LAN meeting sync test (host + client in-process)."""
import sys
import time
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # 项目根目录
sys.path.insert(0, str(ROOT))

from utils.gamemeet_net import MeetHost, MeetClient, get_lan_ipv4

events = {"roster_h": [], "roster_c": [], "doc_c": [], "welcome": []}
lock = threading.Lock()

def run():
    host = MeetHost(
        display_name="HostUser",
        on_roster=lambda u: events["roster_h"].append(list(u)),
        on_doc=lambda r, t, f: None,
        on_status=lambda s: print("[H]", s),
        on_error=lambda e: print("[H ERR]", e),
    )
    ip, port = host.start(preferred_port=0)
    print(f"host listening {ip}:{port} lan={get_lan_ipv4()}")
    assert port > 0

    # host publishes initial
    host.publish_doc("hello from host", from_id=host.host_id)
    time.sleep(0.1)

    client_got = threading.Event()
    client_edit_ok = threading.Event()

    def on_doc_c(rev, text, fid):
        with lock:
            events["doc_c"].append((rev, text, fid))
        if text == "hello from host":
            client_got.set()
        if text == "client edit":
            client_edit_ok.set()

    def on_roster_c(users):
        with lock:
            events["roster_c"].append(list(users))
        me = next((u for u in users if u.get("id") == client.user_id), None)
        # stored later

    client = MeetClient(
        display_name="ClientUser",
        on_roster=on_roster_c,
        on_doc=on_doc_c,
        on_status=lambda s: print("[C]", s),
        on_error=lambda e: print("[C ERR]", e),
        on_welcome=lambda uid, rev, text: events["welcome"].append((uid, rev, text)),
    )
    client.connect("127.0.0.1", port)
    assert client_got.wait(5), "client did not receive initial doc"
    time.sleep(0.2)
    assert client.user_id
    assert len(events["welcome"]) >= 1
    print("welcome", events["welcome"][-1])

    # client cannot edit yet
    client.publish_doc("should be ignored")
    time.sleep(0.3)
    assert host.current_text() == "hello from host", host.current_text()

    # host grants edit
    host.set_edit(client.user_id, True)
    time.sleep(0.3)
    assert client.local_can_edit(), "client should have edit after grant"

    client.publish_doc("client edit")
    assert client_edit_ok.wait(5), "host/client did not sync client edit"
    time.sleep(0.2)
    assert host.current_text() == "client edit", host.current_text()

    # revoke
    host.set_edit(client.user_id, False)
    time.sleep(0.3)
    assert not client.local_can_edit()

    # stale rev ignore: craft low rev by calling on_doc path — just publish new from host
    host.publish_doc("final", from_id=host.host_id)
    time.sleep(0.3)
    assert any(t == "final" for _, t, _ in events["doc_c"])

    client.disconnect()
    time.sleep(0.3)
    # roster should shrink to host only
    last = events["roster_h"][-1]
    assert len(last) == 1 and last[0]["is_host"], last

    host.stop()
    print("ALL NET TESTS PASSED")

if __name__ == "__main__":
    run()