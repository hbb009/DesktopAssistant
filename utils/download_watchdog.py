# utils/download_watchdog.py
# 下载防卡死看门狗
# 需求：任何一条视频/图片下载，如果 180 秒内没有任何进度（无数据流入），
#       就判定为「无法下载」，主动掐断底层 socket 并把该项记为失败，避免
#       整个下载任务被一条永远下不完的链接卡死。
# 注意：只按「静默时长」判定，不按总时长——大文件/慢速网络下正常下载
#       完全可能超过 180 秒，只要一直在收到数据就不算卡住。

import socket
import threading
import time

STALL_SECONDS = 180.0   # 默认单条内容最长时间 / 最长静默时间
CHECK_INTERVAL = 5.0    # 监控线程巡检间隔


class StalledDownloadError(Exception):
    """单条内容超时无响应：视为无法下载。"""


def find_socket(obj):
    """从 requests / urllib 响应对象里递归找出底层 socket，找不到返回 None。

    各版本属性路径不一致（r.raw._connection.sock / r.raw._fp.fp.raw._sock
    / urllib 的 resp.fp.raw._sock 等），这里做防御式递归遍历。
    """
    if obj is None:
        return None
    _seen = set()

    def walk(o, depth):
        if o is None or depth > 8 or id(o) in _seen:
            return None
        _seen.add(id(o))
        if isinstance(o, socket.socket):
            return o
        for name in ("sock", "_sock", "raw", "_fp", "fp", "_connection",
                     "_fp2", "_network", "_original_response"):
            try:
                child = getattr(o, name, None)
            except Exception:
                continue
            if child is not None and child is not o:
                r = walk(child, depth + 1)
                if r:
                    return r
        return None

    try:
        return walk(obj, 0)
    except Exception:
        return None


class DownloadWatchdog:
    """跟踪若干「内容」的下载活跃度；超时则标记 dead 并掐断其 socket。

    用法（每个后台下载线程自建一个实例，全部结束调用 stop()）：
        wd = DownloadWatchdog()          # 默认 180s
        wd.begin(key)                    # 某条内容开始下载
        # chunk 循环里，每次拿到数据后：
        wd.touch(key)                    # 报告有进度
        if wd.is_dead(key):
            raise StalledDownloadError(...)
        wd.attach_socket(key, sock)      # 让看门狗能掐断阻塞读
        wd.end(key)                      # 该条完成 / 放弃
        wd.stop()                        # 全部结束，停止监控线程
    """

    def __init__(self, stall_seconds: float = STALL_SECONDS,
                 interval: float = CHECK_INTERVAL):
        self._stall = max(10.0, float(stall_seconds))
        self._interval = max(1.0, float(interval))
        self._lock = threading.Lock()
        self._items = {}    # key -> {"start": ts, "last": ts}
        self._dead = set()
        self._sockets = {}  # key -> [socket, ...]
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="dl-watchdog"
        )
        self._thread.start()

    @property
    def stall_seconds(self) -> float:
        return self._stall

    def begin(self, key):
        with self._lock:
            self._items[key] = {"start": time.time(), "last": time.time()}
            self._dead.discard(key)

    def touch(self, key):
        with self._lock:
            rec = self._items.get(key)
            if rec is not None:
                rec["last"] = time.time()

    def attach_socket(self, key, sock):
        if sock is None:
            return
        with self._lock:
            if key in self._items:
                self._sockets.setdefault(key, []).append(sock)

    def is_dead(self, key) -> bool:
        with self._lock:
            return key in self._dead

    def end(self, key):
        with self._lock:
            self._items.pop(key, None)
            self._sockets.pop(key, None)
            self._dead.discard(key)

    def _run(self):
        while not self._stop.wait(self._interval):
            now = time.time()
            with self._lock:
                for k, rec in list(self._items.items()):
                    # 只按「静默时长」判定：180 秒没有任何进度才视为无法下载。
                    # 不能按总时长判——大文件/慢速网络下正常下载可能超过 180 秒。
                    if now - rec["last"] > self._stall:
                        self._dead.add(k)
                        for s in self._sockets.get(k, ()):
                            try:
                                s.shutdown(socket.SHUT_RDWR)
                            except Exception:
                                pass
                            try:
                                s.close()
                            except Exception:
                                pass

    def stop(self):
        self._stop.set()
