# utils/gamemeet_net.py
# 游戏会议 — TCP JSON-lines 局域网同步 + UDP 房间发现（stdlib socket + threading）
# 协议：hello / welcome / nick_assign / roster / set_edit / doc / history / history_add / history_del / ping / bye
# 发现：UDP broadcast room_announce（与 TCP 分离）
from __future__ import annotations

import json
import socket
import threading
import time
import uuid
from typing import Callable, Optional

DEFAULT_PORT_HINT = 19517  # 展示用偏好端口；实际仍 bind 空闲口
DISCOVER_PORT = 19518      # UDP 房间广播端口
ANNOUNCE_INTERVAL = 1.5    # 主机通告间隔（秒）
ROOM_STALE_SEC = 5.0       # 无通告后移除房间

HISTORY_CAP = 50

# 轻量调试轨迹（进程内）；单元测试 / 排障可读最近事件
_DEBUG_EVENTS: list = []


def debug_log(event: str, **fields) -> None:
    """记录昵称冲突 / 历史合成等关键事件（环形缓冲）。"""
    rec = {"event": str(event), "ts": time.time()}
    rec.update(fields)
    _DEBUG_EVENTS.append(rec)
    del _DEBUG_EVENTS[:-200]


def recent_debug_events(n: int = 50) -> list:
    return list(_DEBUG_EVENTS[-max(1, int(n)):])


# 《聚宝仙盆》及相关太虚门/秀竹峰风格名号（约 60，加入时昵称冲突随机改用）
JUBAO_XIANPEN_NAMES = (
    # 确认出场/相关
    "贺平生", "乔慧珠", "乔小乔", "玲珑", "郝云",
    "枯木真人", "玉宁真人", "王敦", "程老伯", "萧不凡",
    "白尚礼", "玉德", "冯道姑", "詹师兄", "碧云老祖", "玉玄师伯",
    # 太虚门 / 秀竹峰风格补全
    "沈聚宝", "林仙儿", "陆青云", "苏婉清", "叶无心",
    "赵灵犀", "陈玄机", "周清玄", "吴望舒", "郑明远",
    "何清风", "罗烟萝", "高子衿", "梁问道", "韩雪衣",
    "曹寒烟", "许清歌", "邓云裳", "冯知微", "程慕白",
    "袁听雨", "蒋含光", "沈墨言", "顾青萝", "裴云深",
    "白素问", "谢灵筠", "唐映雪", "宋清和", "方若水",
    "丁元化", "姜清夜", "崔抱朴", "魏无涯", "潘灵秀",
    "余问心", "杜听风", "钟寒山", "汪栖竹", "田漱石",
    "任云鹤", "陆怀真", "尹清秋", "夏听澜", "莫问尘",
    "柳如烟", "花映月", "石抱一", "金玉堂", "水清涟",
)


def _nick_key(name: str) -> str:
    """规范化键：去首尾空白后大小写折叠（中文不变，英文不区分大小写）。"""
    return (name or "").strip().casefold()


def existing_nick_keys(names) -> set:
    keys = set()
    for n in names or []:
        k = _nick_key(str(n or ""))
        if k:
            keys.add(k)
    return keys


def pick_unique_nickname(preferred: str, taken_names, rng=None) -> tuple:
    """若 preferred 与现有成员（含主机）显示名冲突，从 JUBAO_XIANPEN_NAMES 随机未占用名。

    比较用 _nick_key（去空白 + casefold）。返回 (final_name, renamed: bool)。
    """
    import random as _random
    preferred = (preferred or "").strip() or "用户"
    taken = existing_nick_keys(taken_names)
    if _nick_key(preferred) not in taken:
        return preferred, False
    pool = [n for n in JUBAO_XIANPEN_NAMES if _nick_key(n) not in taken]
    if not pool:
        base = preferred
        for i in range(2, 1000):
            cand = f"{base}{i}"
            if _nick_key(cand) not in taken:
                debug_log("nick_collision", preferred=preferred, assigned=cand, taken=list(taken)[:20])
                return cand, True
        cand = f"{base}-{uuid.uuid4().hex[:4]}"
        debug_log("nick_collision", preferred=preferred, assigned=cand, taken=list(taken)[:20])
        return cand, True
    r = rng if rng is not None else _random
    assigned = r.choice(pool)
    debug_log("nick_collision", preferred=preferred, assigned=assigned, taken=list(taken)[:20])
    return assigned, True


def _is_private_ipv4(ip: str) -> bool:
    if not ip or ip.startswith("127."):
        return False
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    if a == 10:
        return True
    if a == 192 and b == 168:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    return False


def get_lan_ipv4() -> str:
    """本机局域网 IPv4（优先 10/172.16-31/192.168，跳过 127.0.0.1）。"""
    candidates = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                candidates.append(ip)
        finally:
            s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in candidates:
                candidates.append(ip)
    except Exception:
        pass
    for ip in candidates:
        if _is_private_ipv4(ip):
            return ip
    for ip in candidates:
        return ip
    return "127.0.0.1"


def history_preview(text: str, max_len: int = 36) -> str:
    """历史列表仅展示首行预览。"""
    raw = text if text is not None else ""
    first = raw.split("\n", 1)[0].strip().replace("\r", "")
    if not first:
        return "（空）"
    if len(first) > max_len:
        return first[:max_len] + "…"
    return first


def _common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def doc_change_should_record(old, new) -> bool:
    """判断文档变更是否值得记入历史（大变 / 空→有；小改/清空不记）。

    规则：
    - new.strip() 空 → False
    - old.strip() 空且 new.strip() 非空 → True（空→有）
    - old == new → False
    - 大变 → True：绝对长度差 >= 40，或相对差 >= 0.35，
      或（双方长度均 >= 8 且非轻微追加/子串，且公共前缀比 < 0.5 且变更跨度 >= 12）
    - 小改/错字 → False
    """
    old = "" if old is None else str(old)
    new = "" if new is None else str(new)
    if not new.strip():
        return False
    if not old.strip():
        return True
    if old == new:
        return False
    lo, ln = len(old), len(new)
    abs_diff = abs(ln - lo)
    if abs_diff >= 40:
        return True
    if abs_diff / max(lo, ln, 1) >= 0.35:
        return True
    if lo >= 8 and ln >= 8:
        minor = bool(new.startswith(old) or old.startswith(new))
        if not minor and abs_diff < 12 and (old in new or new in old):
            minor = True
        if not minor:
            cpl = _common_prefix_len(old, new)
            ratio = cpl / max(lo, ln, 1)
            changed = max(lo, ln) - cpl
            if ratio < 0.5 and changed >= 12:
                return True
    return False


_RECV_BUF_MAX = 2 * 1024 * 1024  # 2MB soft cap — avoid runaway buffering


def _safe_close_sock(sock: Optional[socket.socket]) -> None:
    """shutdown(SHUT_RDWR) then close; never raise; tolerate already-closed."""
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    except Exception:
        pass
    try:
        sock.close()
    except OSError:
        pass
    except Exception:
        pass


def _send_line(sock: socket.socket, obj: dict) -> None:
    if sock is None:
        raise OSError("socket is None")
    data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        sock.sendall(data)
    except (OSError, ConnectionError, TimeoutError, ValueError, AttributeError) as e:
        raise OSError(f"send failed: {e}") from e


def _recv_lines(sock: socket.socket, buf: bytearray):
    """从 socket 读入，产出完整 JSON 行 dict。关闭/错误时干净停止生成器。"""
    if sock is None:
        return
    while True:
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            # 交给调用方做 ping keepalive；勿当断开处理
            raise
        except TimeoutError:
            raise
        except (OSError, ConnectionError, ValueError, AttributeError):
            return
        except Exception:
            return
        if not chunk:
            return
        buf.extend(chunk)
        if len(buf) > _RECV_BUF_MAX:
            # soft stop: clear runaway buffer, end session cleanly
            del buf[:]
            return
        while True:
            nl = buf.find(b"\n")
            if nl < 0:
                break
            line = bytes(buf[:nl]).decode("utf-8", errors="replace").strip()
            del buf[: nl + 1]
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


# ---------------------------------------------------------------------------
# UDP 房间发现
# ---------------------------------------------------------------------------

class RoomAnnouncer:
    """主机侧：周期性 UDP 广播本房间（title / nick / ip / tcp port）。"""

    def __init__(self, host_id: str, nick: str, tcp_port: int, lan_ip: str = ""):
        self.host_id = host_id
        self.nick = nick or "主机"
        self.tcp_port = int(tcp_port)
        self.lan_ip = lan_ip or get_lan_ipv4()
        self._running = False
        self._thr: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def set_nick(self, nick: str):
        with self._lock:
            self.nick = (nick or "").strip() or "主机"

    def set_endpoint(self, lan_ip: str, tcp_port: int):
        with self._lock:
            if lan_ip:
                self.lan_ip = lan_ip
            self.tcp_port = int(tcp_port)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thr = threading.Thread(target=self._loop, name="MeetRoomAnnounce", daemon=True)
        self._thr.start()

    def stop(self):
        self._running = False

    def _payload(self) -> bytes:
        with self._lock:
            nick = self.nick
            ip = self.lan_ip
            port = self.tcp_port
            hid = self.host_id
        title = f"{nick}的会议"
        obj = {
            "type": "room_announce",
            "v": 1,
            "host_id": hid,
            "nick": nick,
            "title": title,
            "ip": ip,
            "port": port,
            "ts": time.time(),
        }
        return json.dumps(obj, ensure_ascii=False).encode("utf-8")

    def _loop(self):
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            while self._running:
                data = self._payload()
                targets = [("255.255.255.255", DISCOVER_PORT), ("127.0.0.1", DISCOVER_PORT)]
                try:
                    with self._lock:
                        ip = self.lan_ip
                    parts = (ip or "").split(".")
                    if len(parts) == 4:
                        targets.append((".".join(parts[:3] + ["255"]), DISCOVER_PORT))
                except Exception:
                    pass
                for target in targets:
                    try:
                        sock.sendto(data, target)
                    except Exception:
                        pass
                end = time.time() + ANNOUNCE_INTERVAL
                while self._running and time.time() < end:
                    time.sleep(0.1)
        except Exception:
            pass
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass


class RoomBrowser:
    """监听 UDP 通告，维护可加入房间列表；超时剔除。"""

    def __init__(self, on_rooms=None):
        self.on_rooms: Optional[Callable] = on_rooms
        self._rooms = {}  # key -> dict
        self._lock = threading.Lock()
        self._running = False
        self._thr: Optional[threading.Thread] = None
        self._prune_thr: Optional[threading.Thread] = None
        self._sock: Optional[socket.socket] = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thr = threading.Thread(target=self._listen_loop, name="MeetRoomBrowse", daemon=True)
        self._thr.start()
        self._prune_thr = threading.Thread(target=self._prune_loop, name="MeetRoomPrune", daemon=True)
        self._prune_thr.start()

    def stop(self):
        self._running = False
        sock = self._sock
        self._sock = None
        _safe_close_sock(sock)
        with self._lock:
            self._rooms.clear()
        self._emit()

    def snapshot(self) -> list:
        with self._lock:
            return [dict(v) for v in self._rooms.values()]

    def _emit(self):
        if not self.on_rooms:
            return
        try:
            self.on_rooms(self.snapshot())
        except Exception:
            pass

    def _room_key(self, msg: dict) -> str:
        hid = str(msg.get("host_id") or "")
        if hid:
            return "id:" + hid
        ip = str(msg.get("ip") or "")
        port = int(msg.get("port") or 0)
        return f"ep:{ip}:{port}"

    def _listen_loop(self):
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except Exception:
                pass
            sock.bind(("0.0.0.0", DISCOVER_PORT))
            sock.settimeout(1.0)
            self._sock = sock
            while self._running:
                try:
                    data, addr = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                except (OSError, ConnectionError, TimeoutError, ValueError, AttributeError):
                    break
                except Exception:
                    break
                try:
                    msg = json.loads(data.decode("utf-8", errors="replace"))
                except Exception:
                    continue
                if not isinstance(msg, dict) or msg.get("type") != "room_announce":
                    continue
                ip = str(msg.get("ip") or "").strip() or addr[0]
                try:
                    port = int(msg.get("port") or 0)
                except Exception:
                    continue
                if port <= 0 or port > 65535:
                    continue
                nick = str(msg.get("nick") or "").strip() or "主机"
                title = str(msg.get("title") or "").strip() or f"{nick}的会议"
                host_id = str(msg.get("host_id") or "")
                key = self._room_key({**msg, "ip": ip, "port": port})
                entry = {
                    "key": key,
                    "host_id": host_id,
                    "nick": nick,
                    "title": title,
                    "ip": ip,
                    "port": port,
                    "seen": time.time(),
                }
                with self._lock:
                    prev = self._rooms.get(key)
                    self._rooms[key] = entry
                    changed = prev is None or (
                        prev.get("ip") != ip
                        or prev.get("port") != port
                        or prev.get("title") != title
                        or prev.get("nick") != nick
                    )
                if changed:
                    self._emit()
        except Exception:
            pass
        finally:
            if self._sock is sock:
                self._sock = None
            _safe_close_sock(sock)

    def _prune_loop(self):
        while self._running:
            time.sleep(1.0)
            now = time.time()
            removed = False
            with self._lock:
                dead = [
                    k for k, v in self._rooms.items()
                    if now - float(v.get("seen") or 0) > ROOM_STALE_SEC
                ]
                for k in dead:
                    self._rooms.pop(k, None)
                    removed = True
            if removed:
                self._emit()


class MeetHost:
    """会议主机：权威用户列表 / 编辑权 / 文档 / 历史。"""

    def __init__(
        self,
        display_name: str = "主机",
        on_roster=None,
        on_doc=None,
        on_status=None,
        on_error=None,
        on_history=None,
    ):
        self.display_name = display_name or "主机"
        self.on_roster: Optional[Callable] = on_roster
        self.on_doc: Optional[Callable] = on_doc
        self.on_status: Optional[Callable] = on_status
        self.on_error: Optional[Callable] = on_error
        self.on_history: Optional[Callable] = on_history

        self._sock: Optional[socket.socket] = None
        self._accept_th: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._running = False
        self.host_id = "host-" + uuid.uuid4().hex[:8]
        self.port = 0
        self.lan_ip = get_lan_ipv4()
        self._clients = {}  # user_id -> {sock, name, can_edit, ...}
        self._rev = 0
        self._text = ""
        self._history = []  # [{id, text, preview, ts}, ...] 新在前
        self._announcer: Optional[RoomAnnouncer] = None

    # ---- public ----
    def start(self, preferred_port: int = 0) -> tuple:
        """绑定 0.0.0.0，返回 (lan_ip, port)。preferred_port=0 或占用则系统分配。"""
        if self._running:
            return self.lan_ip, self.port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        port = int(preferred_port or 0)
        try:
            sock.bind(("0.0.0.0", port))
        except OSError:
            sock.bind(("0.0.0.0", 0))
        sock.listen(16)
        sock.settimeout(1.0)
        self._sock = sock
        self.port = sock.getsockname()[1]
        self.lan_ip = get_lan_ipv4()
        self._running = True
        self._accept_th = threading.Thread(
            target=self._accept_loop, name="MeetHostAccept", daemon=True
        )
        self._accept_th.start()
        self._announcer = RoomAnnouncer(
            host_id=self.host_id,
            nick=self.display_name,
            tcp_port=self.port,
            lan_ip=self.lan_ip,
        )
        self._announcer.start()
        self._emit_status(f"主持中 {self.lan_ip}:{self.port}")
        self._emit_roster()
        self._emit_history()
        return self.lan_ip, self.port

    def stop(self):
        self._running = False
        if self._announcer:
            try:
                self._announcer.stop()
            except Exception:
                pass
            self._announcer = None
        with self._lock:
            clients = list(self._clients.items())
            self._clients.clear()
        for uid, info in clients:
            try:
                _send_line(info["sock"], {"type": "bye", "reason": "host_left"})
            except Exception:
                pass
            _safe_close_sock(info.get("sock"))
        listen = self._sock
        self._sock = None
        _safe_close_sock(listen)
        self._emit_status("未连接")
        self._emit_roster()

    def set_display_name(self, name: str):
        self.display_name = (name or "").strip() or "主机"
        if self._announcer:
            self._announcer.set_nick(self.display_name)
        self._emit_roster()
        self._broadcast({"type": "roster", "users": self._roster_list()}, exclude=None)

    def set_edit(self, user_id: str, can_edit: bool):
        with self._lock:
            if user_id == self.host_id:
                can_edit = True
            elif user_id in self._clients:
                self._clients[user_id]["can_edit"] = bool(can_edit)
            else:
                return
        self._emit_roster()
        self._broadcast({"type": "roster", "users": self._roster_list()}, exclude=None)

    def _append_history_for_text_locked(self, text: str):
        """为 NEW 文本插入历史（若最新条已相同则跳过）。调用方须持有 self._lock。"""
        text = text if text is not None else ""
        if not str(text).strip():
            return None
        newest = self._history[0] if self._history else None
        if newest is not None and str(newest.get("text") or "") == text:
            return None
        entry = {
            "id": "h-" + uuid.uuid4().hex[:10],
            "text": text,
            "preview": history_preview(text),
            "ts": time.time(),
        }
        self._history.insert(0, entry)
        del self._history[HISTORY_CAP:]
        return entry

    def publish_doc(
        self,
        text: str,
        from_id: Optional[str] = None,
        *,
        consider_history: bool = False,
    ):
        """主机本地编辑或校验后的客户端更新。

        consider_history=True 时按 doc_change_should_record 归档 NEW 文本。
        """
        from_id = from_id or self.host_id
        text = text if text is not None else ""
        entry = None
        hist_snapshot = None
        with self._lock:
            old = self._text if self._text is not None else ""
            if consider_history and doc_change_should_record(old, text):
                entry = self._append_history_for_text_locked(text)
                if entry is not None:
                    hist_snapshot = [dict(x) for x in self._history]
            self._rev += 1
            self._text = text
            rev = self._rev
            payload = {"type": "doc", "rev": rev, "text": self._text, "from": from_id}
        self._broadcast(payload, exclude=None)
        if entry is not None:
            self._broadcast({"type": "history_add", "entry": entry}, exclude=None)
            self._broadcast({"type": "history", "items": hist_snapshot}, exclude=None)
        if self.on_doc:
            try:
                self.on_doc(rev, self._text, from_id)
            except Exception:
                pass
        if entry is not None and self.on_history:
            try:
                self.on_history(hist_snapshot)
            except Exception:
                pass

    def replace_doc_from_paste(
        self,
        text: str,
        from_id: Optional[str] = None,
        *,
        prior_text: Optional[str] = None,
    ):
        """主机粘贴完成：按 doc_change_should_record 将 NEW 文本进历史，再广播 doc + history_add。

        prior_text: 粘贴前编辑器正文，优先作为 old（避免 _text 与 UI 不同步）。
        """
        from_id = from_id or self.host_id
        text = text if text is not None else ""
        entry = None
        hist_snapshot = None
        with self._lock:
            if prior_text is not None:
                old = prior_text
            else:
                old = self._text if self._text is not None else ""
            if old is None:
                old = ""
            if old == text:
                self._text = text
                return self._rev
            if doc_change_should_record(old, text):
                entry = self._append_history_for_text_locked(text)
            self._rev += 1
            self._text = text
            rev = self._rev
            doc_payload = {"type": "doc", "rev": rev, "text": self._text, "from": from_id}
            hist_snapshot = [dict(x) for x in self._history]
        self._broadcast(doc_payload, exclude=None)
        if entry:
            self._broadcast({"type": "history_add", "entry": entry}, exclude=None)
            self._broadcast({"type": "history", "items": hist_snapshot}, exclude=None)
        if self.on_doc:
            try:
                self.on_doc(rev, text, from_id)
            except Exception:
                pass
        if entry and self.on_history:
            try:
                self.on_history(hist_snapshot)
            except Exception:
                pass
        return rev


    def replace_doc_from_clipboard(self, text: str, from_id: Optional[str] = None):
        """兼容旧名；历史触发已改为粘贴路径。"""
        return self.replace_doc_from_paste(text, from_id=from_id)


    def delete_history(self, entry_id: str) -> bool:
        """删除一条历史并广播 history_del；成功返回 True。"""
        eid = str(entry_id or "").strip()
        if not eid:
            return False
        with self._lock:
            before = len(self._history)
            self._history = [x for x in self._history if str(x.get("id") or "") != eid]
            after = len(self._history)
            snap = [dict(x) for x in self._history]
        if before == after:
            return False
        self._broadcast({"type": "history_del", "id": eid}, exclude=None)
        if self.on_history:
            try:
                self.on_history(snap)
            except Exception:
                pass
        return True

    def seed_doc(self, text: str) -> None:
        """创建房间时把编辑器已有正文写入权威副本（不广播、不归档）。"""
        with self._lock:
            self._text = text if text is not None else ""

    def current_text(self) -> str:
        with self._lock:
            return self._text

    def current_rev(self) -> int:
        with self._lock:
            return self._rev

    def current_history(self) -> list:
        with self._lock:
            return [dict(x) for x in self._history]

    def _ensure_doc_history_locked(self) -> bool:
        """确保当前正文在历史最前有对应卡片。

        不只在历史为空时：若当前文档非空，且没有任何条目文本等于当前正文，
        或最新一条与当前正文不同，则在最前插入一条。调用方须已持有 self._lock。
        返回是否新增。
        """
        text = self._text if self._text is not None else ""
        if not str(text).strip():
            return False
        newest = self._history[0] if self._history else None
        if newest is not None and str(newest.get("text") or "") == text:
            return False
        # 任意旧条目已有相同正文但最新不同 → 仍插入，保证当前正文是最新卡
        entry = {
            "id": "h-" + uuid.uuid4().hex[:10],
            "text": text,
            "preview": history_preview(text),
            "ts": time.time(),
        }
        self._history.insert(0, entry)
        del self._history[HISTORY_CAP:]
        debug_log("history_synthesize", entry_id=entry["id"], preview=entry["preview"])
        return True

    def ensure_doc_history(self) -> bool:
        """确保当前正文有历史卡（必要时合成）并通知本机 on_history。"""
        added = False
        snap = None
        with self._lock:
            added = self._ensure_doc_history_locked()
            if added:
                snap = [dict(x) for x in self._history]
        if added and snap is not None and self.on_history:
            try:
                self.on_history(snap)
            except Exception:
                pass
        return added

    def _taken_names(self, exclude_uid: Optional[str] = None) -> list:
        """现有成员显示名（含主机），用于昵称冲突检测。"""
        names = [self.display_name]
        with self._lock:
            for uid, info in self._clients.items():
                if exclude_uid and uid == exclude_uid:
                    continue
                names.append(info.get("name"))
        return names

    def local_can_edit(self) -> bool:
        return True

    def local_user_id(self) -> str:
        return self.host_id

    # ---- internal ----
    def _emit_status(self, msg: str):
        if self.on_status:
            try:
                self.on_status(msg)
            except Exception:
                pass

    def _emit_error(self, msg: str):
        if self.on_error:
            try:
                self.on_error(msg)
            except Exception:
                pass

    def _emit_history(self):
        if self.on_history:
            try:
                self.on_history(self.current_history())
            except Exception:
                pass

    def _roster_list(self) -> list:
        users = [
            {"id": self.host_id, "name": self.display_name, "can_edit": True, "is_host": True}
        ]
        with self._lock:
            for uid, info in self._clients.items():
                users.append({
                    "id": uid,
                    "name": info["name"],
                    "can_edit": bool(info["can_edit"]),
                    "is_host": False,
                })
        return users

    def _emit_roster(self):
        if self.on_roster:
            try:
                self.on_roster(self._roster_list())
            except Exception:
                pass

    def _broadcast(self, obj: dict, exclude=None):
        with self._lock:
            items = list(self._clients.items())
        dead = []
        for uid, info in items:
            if exclude and uid == exclude:
                continue
            try:
                _send_line(info["sock"], obj)
            except Exception:
                dead.append(uid)
        for uid in dead:
            self._drop_client(uid)

    def _accept_loop(self):
        while self._running and self._sock:
            try:
                conn, addr = self._sock.accept()
            except socket.timeout:
                continue
            except (OSError, ConnectionError, TimeoutError, ValueError, AttributeError):
                break
            except Exception as e:
                self._emit_error(f"接受连接失败: {e}")
                break
            thr = threading.Thread(
                target=self._client_loop,
                args=(conn, addr),
                name=f"MeetHostClient-{addr[0]}",
                daemon=True,
            )
            thr.start()

    def _client_loop(self, conn: socket.socket, addr):
        conn.settimeout(60.0)
        buf = bytearray()
        user_id = None
        try:
            deadline = time.time() + 15
            hello = None
            while time.time() < deadline and hello is None:
                try:
                    for msg in _recv_lines(conn, buf):
                        if msg.get("type") == "hello":
                            hello = msg
                            break
                        if msg.get("type") == "bye":
                            return
                except socket.timeout:
                    continue
            if not hello:
                return
            raw_name = str(hello.get("name") or "").strip() or f"用户{addr[1]}"
            user_id = "u-" + uuid.uuid4().hex[:8]
            with self._lock:
                # 冲突检测必须含主机 display_name + 已有客户端（加入花名册之前）
                taken = [self.display_name] + [
                    info.get("name") for info in self._clients.values()
                ]
                name, nick_renamed = pick_unique_nickname(raw_name, taken)
                self._clients[user_id] = {
                    "sock": conn,
                    "name": name,
                    "can_edit": False,
                    "addr": addr,
                    "buf": buf,
                }
                # 历史空但文档非空 → 合成一条，保证加入者有历史卡
                self._ensure_doc_history_locked()
                rev = self._rev
                text = self._text
                history = [dict(x) for x in self._history]
            try:
                _send_line(conn, {
                    "type": "welcome",
                    "user_id": user_id,
                    "host_id": self.host_id,
                    "rev": rev,
                    "text": text,
                    "name": name,
                    "nick_renamed": bool(nick_renamed),
                    "history": history,
                })
                # 冗余：重名时再发 nick_assign，避免客户端只听 assign 或 welcome 丢字段
                if nick_renamed:
                    _send_line(conn, {
                        "type": "nick_assign",
                        "name": name,
                        "nick_renamed": True,
                    })
                _send_line(conn, {"type": "roster", "users": self._roster_list()})
                _send_line(conn, {"type": "history", "items": history})
            except Exception:
                self._drop_client(user_id)
                return
            self._emit_roster()
            self._broadcast({"type": "roster", "users": self._roster_list()}, exclude=None)
            self._emit_status(f"{name} 已加入")

            while self._running:
                try:
                    got = False
                    for msg in _recv_lines(conn, buf):
                        got = True
                        self._handle_client_msg(user_id, msg)
                    if not got:
                        break
                except socket.timeout:
                    try:
                        _send_line(conn, {"type": "ping"})
                    except Exception:
                        break
                    continue
                except (OSError, ConnectionError, TimeoutError, ValueError, AttributeError):
                    break
                except Exception:
                    break
        except Exception as e:
            self._emit_error(f"客户端会话异常: {e}")
        finally:
            if user_id:
                self._drop_client(user_id)
            else:
                _safe_close_sock(conn)

    def _handle_client_msg(self, user_id: str, msg: dict):
        mtype = msg.get("type")
        if mtype == "bye":
            self._drop_client(user_id)
            return
        if mtype == "ping":
            with self._lock:
                info = self._clients.get(user_id)
            if info:
                try:
                    _send_line(info["sock"], {"type": "ping"})
                except Exception:
                    self._drop_client(user_id)
            return
        if mtype == "hello":
            name = str(msg.get("name") or "").strip()
            if name:
                final = name
                renamed = False
                sock = None
                with self._lock:
                    if user_id in self._clients:
                        taken = [self.display_name] + [
                            info.get("name")
                            for uid, info in self._clients.items()
                            if uid != user_id
                        ]
                        final, renamed = pick_unique_nickname(name, taken)
                        self._clients[user_id]["name"] = final
                        sock = self._clients[user_id].get("sock")
                if sock is not None and (renamed or _nick_key(final) != _nick_key(name) or final != name):
                    try:
                        _send_line(sock, {
                            "type": "nick_assign",
                            "name": final,
                            "nick_renamed": True,
                        })
                    except Exception:
                        self._drop_client(user_id)
                        return
                self._emit_roster()
                self._broadcast({"type": "roster", "users": self._roster_list()}, exclude=None)
            return
        if mtype == "doc":
            with self._lock:
                info = self._clients.get(user_id)
                if not info or not info.get("can_edit"):
                    return
                text = msg.get("text")
                if text is None:
                    return
                self._rev += 1
                self._text = str(text)
                rev = self._rev
                payload = {"type": "doc", "rev": rev, "text": self._text, "from": user_id}
            self._broadcast(payload, exclude=None)
            if self.on_doc:
                try:
                    self.on_doc(rev, self._text, user_id)
                except Exception:
                    pass
            return
        if mtype == "set_edit":
            return

    def _drop_client(self, user_id: str):
        with self._lock:
            info = self._clients.pop(user_id, None)
        if not info:
            return
        _safe_close_sock(info.get("sock"))
        name = info.get("name") or user_id
        self._emit_roster()
        self._broadcast({"type": "roster", "users": self._roster_list()}, exclude=None)
        self._emit_status(f"{name} 已离开")


class MeetClient:
    """会议客户端。"""

    def __init__(
        self,
        display_name: str = "用户",
        on_roster=None,
        on_doc=None,
        on_status=None,
        on_error=None,
        on_welcome=None,
        on_history=None,
        on_history_del=None,
        on_nick_assign=None,
    ):
        self.display_name = display_name or "用户"
        self.on_roster = on_roster
        self.on_doc = on_doc
        self.on_status = on_status
        self.on_error = on_error
        self.on_welcome = on_welcome
        self.on_history = on_history
        self.on_history_del = on_history_del
        self.on_nick_assign = on_nick_assign

        self._sock: Optional[socket.socket] = None
        self._thr: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._running = False
        self.user_id = ""
        self.host_id = ""
        self._can_edit = False
        self._rev = 0
        self._text = ""
        self._roster = []
        self._history = []

    def connect(self, host: str, port: int) -> None:
        if self._running:
            self.disconnect()
        host = (host or "").strip()
        port = int(port)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10.0)
        try:
            sock.connect((host, port))
        except Exception as e:
            sock.close()
            raise ConnectionError(f"无法连接 {host}:{port} — {e}") from e
        sock.settimeout(60.0)
        self._sock = sock
        self._running = True
        # hello 名与 UI 展示一致：连接前再规范化一次
        self.display_name = (self.display_name or "").strip() or "用户"
        hello_name = self.display_name
        _send_line(sock, {"type": "hello", "name": hello_name, "role": "client"})
        self._thr = threading.Thread(target=self._loop, name="MeetClient", daemon=True)
        self._thr.start()
        # 状态提示可能与 welcome/nick_assign 竞态；UI 侧会避免覆盖「昵称重复」tip
        self._emit_status(f"已连接 {host}:{port}")

    def disconnect(self):
        was = self._running
        self._running = False
        sock = self._sock
        self._sock = None
        if sock:
            try:
                if was:
                    _send_line(sock, {"type": "bye"})
            except Exception:
                pass
            _safe_close_sock(sock)
        self.user_id = ""
        self._can_edit = False
        self._roster = []
        self._history = []
        self._text = ""
        self._rev = 0
        if was:
            self._emit_status("未连接")
        if self.on_roster:
            try:
                self.on_roster([])
            except Exception:
                pass
        if self.on_history:
            try:
                self.on_history([])
            except Exception:
                pass

    def set_display_name(self, name: str):
        self.display_name = (name or "").strip() or "用户"
        sock = self._sock
        if sock and self._running:
            try:
                _send_line(sock, {"type": "hello", "name": self.display_name, "role": "client"})
            except Exception:
                pass

    def publish_doc(self, text: str):
        if not self._can_edit:
            return
        sock = self._sock
        if not sock or not self._running:
            return
        with self._lock:
            payload = {
                "type": "doc",
                "rev": self._rev,
                "text": text if text is not None else "",
                "from": self.user_id,
            }
        try:
            _send_line(sock, payload)
        except Exception as e:
            self._emit_error(f"发送文档失败: {e}")

    def local_can_edit(self) -> bool:
        return bool(self._can_edit)

    def local_user_id(self) -> str:
        return self.user_id

    def current_rev(self) -> int:
        return self._rev


    def current_text(self) -> str:
        with self._lock:
            return "" if self._text is None else str(self._text)

    def current_roster(self) -> list:
        with self._lock:
            return [dict(x) for x in (self._roster or [])]
    def current_history(self) -> list:
        with self._lock:
            return [dict(x) for x in self._history]

    def _emit_status(self, msg: str):
        if self.on_status:
            try:
                self.on_status(msg)
            except Exception:
                pass

    def _emit_error(self, msg: str):
        if self.on_error:
            try:
                self.on_error(msg)
            except Exception:
                pass

    def _loop(self):
        sock = self._sock
        buf = bytearray()
        try:
            while self._running and sock:
                try:
                    got = False
                    for msg in _recv_lines(sock, buf):
                        got = True
                        self._handle(msg)
                    if not got:
                        break
                except socket.timeout:
                    try:
                        _send_line(sock, {"type": "ping"})
                    except Exception:
                        break
                    continue
                except (OSError, ConnectionError, TimeoutError, ValueError, AttributeError):
                    break
                except Exception:
                    break
        except Exception as e:
            self._emit_error(f"连接异常: {e}")
        finally:
            was = self._running
            self._running = False
            sock = self._sock
            self._sock = None
            if sock is not None:
                _safe_close_sock(sock)
            if was:
                self._emit_status("连接已断开")
            if self.on_roster:
                try:
                    self.on_roster([])
                except Exception:
                    pass

    def _handle(self, msg: dict):
        mtype = msg.get("type")
        if mtype == "welcome":
            self.user_id = str(msg.get("user_id") or "")
            self.host_id = str(msg.get("host_id") or "")
            self._rev = int(msg.get("rev") or 0)
            text = msg.get("text")
            self._text = "" if text is None else str(text)
            assigned = str(msg.get("name") or "").strip()
            prev_name = self.display_name
            nick_renamed = bool(msg.get("nick_renamed"))
            if assigned:
                if _nick_key(assigned) != _nick_key(prev_name):
                    nick_renamed = True
                self.display_name = assigned
            if nick_renamed:
                debug_log(
                    "client_nick_assigned",
                    prev=prev_name,
                    assigned=assigned or self.display_name,
                    via="welcome",
                )
            # welcome 内嵌 history：不依赖后续单独 history 包
            hist_items = msg.get("history")
            if isinstance(hist_items, list):
                cleaned = []
                for it in hist_items:
                    if not isinstance(it, dict):
                        continue
                    txt = str(it.get("text") if it.get("text") is not None else "")
                    cleaned.append({
                        "id": str(it.get("id") or ""),
                        "text": txt,
                        "preview": str(it.get("preview") or history_preview(txt)),
                        "ts": float(it.get("ts") or 0),
                    })
                cleaned = [x for x in cleaned if x.get("id")]
                cleaned.sort(key=lambda x: float(x.get("ts") or 0), reverse=True)
                cleaned = cleaned[:HISTORY_CAP]
                with self._lock:
                    self._history = cleaned
                if self.on_history:
                    try:
                        self.on_history(list(cleaned))
                    except Exception:
                        pass
            if self.on_welcome:
                try:
                    # 兼容：新签名 (uid, rev, text, name, nick_renamed)；旧签名仅 3 参
                    try:
                        self.on_welcome(
                            self.user_id,
                            self._rev,
                            text if text is not None else "",
                            assigned,
                            nick_renamed,
                        )
                    except TypeError:
                        self.on_welcome(self.user_id, self._rev, text if text is not None else "")
                except Exception:
                    pass
            if text is not None:
                self._text = str(text)
                if self.on_doc:
                    try:
                        self.on_doc(self._rev, str(text), "host")
                    except Exception:
                        pass
            return
        if mtype == "nick_assign":
            assigned = str(msg.get("name") or "").strip()
            nick_renamed = bool(msg.get("nick_renamed"))
            if assigned:
                self.display_name = assigned
            if self.on_nick_assign:
                try:
                    self.on_nick_assign(assigned, True)
                except Exception:
                    pass
            return
        if mtype == "roster":
            users = msg.get("users") or []
            self._roster = users
            me = next((u for u in users if u.get("id") == self.user_id), None)
            self._can_edit = bool(me.get("can_edit")) if me else False
            if self.on_roster:
                try:
                    self.on_roster(users)
                except Exception:
                    pass
            return
        if mtype == "doc":
            rev = int(msg.get("rev") or 0)
            if rev < self._rev:
                return
            self._rev = rev
            text = msg.get("text")
            if text is None:
                return
            self._text = str(text)
            if self.on_doc:
                try:
                    self.on_doc(rev, str(text), str(msg.get("from") or ""))
                except Exception:
                    pass
            return
        if mtype == "history":
            items = msg.get("items") or []
            if not isinstance(items, list):
                items = []
            cleaned = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                txt = str(it.get("text") if it.get("text") is not None else "")
                cleaned.append({
                    "id": str(it.get("id") or ""),
                    "text": txt,
                    "preview": str(it.get("preview") or history_preview(txt)),
                    "ts": float(it.get("ts") or 0),
                })
            with self._lock:
                self._history = cleaned
            if self.on_history:
                try:
                    self.on_history(list(cleaned))
                except Exception:
                    pass
            return
        if mtype == "history_add":
            entry = msg.get("entry") or {}
            if not isinstance(entry, dict):
                return
            txt = str(entry.get("text") if entry.get("text") is not None else "")
            item = {
                "id": str(entry.get("id") or ""),
                "text": txt,
                "preview": str(entry.get("preview") or history_preview(txt)),
                "ts": float(entry.get("ts") or 0),
            }
            with self._lock:
                self._history = [x for x in self._history if x.get("id") != item["id"]]
                self._history.insert(0, item)
                del self._history[HISTORY_CAP:]
                snap = list(self._history)
            if self.on_history:
                try:
                    self.on_history(snap)
                except Exception:
                    pass
            return
        if mtype == "history_del":
            eid = str(msg.get("id") or "").strip()
            if not eid:
                return
            with self._lock:
                self._history = [x for x in self._history if str(x.get("id") or "") != eid]
                snap = list(self._history)
            if self.on_history_del:
                try:
                    self.on_history_del(eid)
                except Exception:
                    pass
            if self.on_history:
                try:
                    self.on_history(snap)
                except Exception:
                    pass
            return
        if mtype == "bye":
            self._running = False
            sock = self._sock
            self._sock = None
            if sock is not None:
                _safe_close_sock(sock)
            # was=False in finally → 不会再发「连接已断开」；由 UI 收到本状态后退出会议态
            self._emit_status("主机已结束会议")
            return
        if mtype == "ping":
            return

