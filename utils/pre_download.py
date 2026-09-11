# utils/pre_download.py
# ---------------------------------------------------------------------------
# 预下载记录：剪贴板命中白名单 → 按平台入队；空闲且冷却结束后自动开跑。
# 替代「侧栏自动下载开关 + F6 记录 + 批处理」链路。
# ---------------------------------------------------------------------------

from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional, Tuple

from utils.logger import get_logger

log = get_logger(__name__)

# 六种下载平台（与关于页白名单 key 对应关系见 PLAT_WHITELIST_KEY）
PLATFORMS = (
    "douyin",
    "bilibili",
    "youtube",
    "ehentai",
    "pixiv",
    "hitomi",
)

# 解析失败 / 无法下载：移入此分类，不再自动重试
FAILED_PLAT = "failed"
FAILED_LABEL = "无法处理"

PLAT_LABEL = {
    "douyin": "抖音",
    "bilibili": "B站",
    "youtube": "YouTube",
    "ehentai": "e-hentai",
    "pixiv": "Pixiv",
    "hitomi": "hitomi.la",
    FAILED_PLAT: FAILED_LABEL,
}

# 预处理文件分段：六平台 + 无法处理
SECTION_KEYS = PLATFORMS + (FAILED_PLAT,)

# 平台 → 关于页 clipboard_auto.whitelist 键
PLAT_WHITELIST_KEY = {
    "douyin": "douyin",
    "bilibili": "bilibili",
    "youtube": "youtube",
    "ehentai": "gallery_eh",
    "pixiv": "gallery_pixiv",
    "hitomi": "gallery_hitomi",
}

# 预下载结束后 / 打开自动处理开关后，再次自动激活的间隔（秒）
COOLDOWN_SEC = 3.0


def _store_path() -> str:
    try:
        from utils.app_paths import records_file
        return records_file("pre_download.json")
    except Exception:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        d = os.path.join(root, "data")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        return os.path.join(d, "pre_download.json")


def normalize_line(text: str) -> str:
    """与速存记录一致：压空白为单空格。"""
    return " ".join(((text or "").strip()).split())


class PreDownloadStore:
    """持久化六平台预下载队列 + 无法处理列表 + 冷却时间戳。

    items 是总下载顺序（JSON 里的真实队形）。取消一条就把它挪到 items 末尾
    并立刻 save；重开程序仍从队头继续。queues 由 items 派生，给分平台列表用。
    failed 是解析失败/无法下载的 URL，不参与自动处理。
    """

    def __init__(self):
        self.queues: Dict[str, List[str]] = {k: [] for k in PLATFORMS}
        self.items: List[Tuple[str, str]] = []
        self.failed: List[str] = []
        self.last_finish_ts: float = 0.0
        self._load()

    def _load(self) -> None:
        path = _store_path()
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
            qs = data.get("queues") or {}
            queues: Dict[str, List[str]] = {k: [] for k in PLATFORMS}
            if isinstance(qs, dict):
                for k in PLATFORMS:
                    raw = qs.get(k) or []
                    if isinstance(raw, list):
                        queues[k] = [
                            normalize_line(str(x))
                            for x in raw
                            if normalize_line(str(x))
                        ]
            items = self._parse_pairs(data.get("items"))
            if not items:
                for k in PLATFORMS:
                    for url in queues.get(k) or []:
                        items.append((k, url))
            # 旧版 deferred：补进总队尾，再丢掉独立字段
            for plat, url in self._parse_pairs(data.get("deferred")):
                items = [(p, u) for p, u in items if not (p == plat and u == url)]
                items.append((plat, url))
            self.items = items
            self.failed = self._parse_failed_urls(data.get("failed"))
            # 还在队里的 URL 不算无法处理（用户可能已手动移回）
            in_queue = {u for _p, u in self.items}
            self.failed = [u for u in self.failed if u not in in_queue]
            self._rebuild_queues()
            try:
                self.last_finish_ts = float(data.get("last_finish_ts") or 0)
            except (TypeError, ValueError):
                self.last_finish_ts = 0.0
        except Exception:
            log.exception("读取预下载记录失败")

    @staticmethod
    def _parse_pairs(raw) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        if not isinstance(raw, list):
            return out
        seen = set()
        for item in raw:
            plat, url = "", ""
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                plat, url = str(item[0] or ""), str(item[1] or "")
            elif isinstance(item, dict):
                plat = str(item.get("platform") or item.get("plat") or "")
                url = str(item.get("url") or item.get("text") or "")
            plat = plat.strip()
            url = normalize_line(url)
            if plat not in PLATFORMS or not url:
                continue
            key = (plat, url)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
        return out

    @staticmethod
    def _parse_failed_urls(raw) -> List[str]:
        """兼容 ["url"] / [["plat","url"]] / [{"url":...}]。"""
        out: List[str] = []
        if not isinstance(raw, list):
            return out
        seen = set()
        for item in raw:
            url = ""
            if isinstance(item, str):
                url = normalize_line(item)
            elif isinstance(item, (list, tuple)) and item:
                url = normalize_line(str(item[-1] if len(item) >= 2 else item[0] or ""))
            elif isinstance(item, dict):
                url = normalize_line(str(item.get("url") or item.get("text") or ""))
            if not url or url in seen:
                continue
            seen.add(url)
            out.append(url)
        return out

    def _rebuild_queues(self) -> None:
        self.queues = {k: [] for k in PLATFORMS}
        for plat, url in self.items:
            if plat in self.queues:
                self.queues[plat].append(url)

    def save(self) -> None:
        path = _store_path()
        try:
            self._rebuild_queues()
            payload = {
                "items": [[p, u] for p, u in self.items],
                "queues": {k: list(self.queues.get(k) or []) for k in PLATFORMS},
                "failed": list(self.failed),
                "last_finish_ts": float(self.last_finish_ts or 0),
            }
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:
            log.exception("写入预下载记录失败")

    def list_platform(self, plat: str) -> List[str]:
        if plat == FAILED_PLAT:
            return list(self.failed)
        return list(self.queues.get(plat) or [])

    def replace_queues(self, queues: Dict[str, List[str]]) -> None:
        """整表替换六平台队列 + 无法处理（预处理文件编辑落盘用）；规范化并去空行。"""
        items: List[Tuple[str, str]] = []
        seen = set()
        for k in PLATFORMS:
            raw = (queues or {}).get(k) or []
            if not isinstance(raw, list):
                raw = []
            for x in raw:
                line = normalize_line(str(x))
                key = (k, line)
                if not line or key in seen:
                    continue
                seen.add(key)
                items.append(key)
        in_queue = {u for _p, u in items}
        failed: List[str] = []
        seen_f = set()
        raw_f = (queues or {}).get(FAILED_PLAT) or []
        if not isinstance(raw_f, list):
            raw_f = []
        for x in raw_f:
            line = normalize_line(str(x))
            if not line or line in seen_f or line in in_queue:
                continue
            seen_f.add(line)
            failed.append(line)
        self.items = items
        self.failed = failed
        self._rebuild_queues()
        self.save()

    def total_count(self) -> int:
        return len(self.items)

    def enqueue(self, plat: str, text: str, *, allow_dup: bool = False) -> bool:
        """入队；默认同平台去重。成功返回 True。"""
        plat = (plat or "").strip()
        line = normalize_line(text)
        if plat not in PLATFORMS or not line:
            return False
        if not allow_dup and any(p == plat and u == line for p, u in self.items):
            return False
        # 再次复制同一链接 = 准备重试，从「无法处理」拿回队列
        self.failed = [u for u in self.failed if u != line]
        self.items.append((plat, line))
        self._rebuild_queues()
        self.save()
        return True

    def remove_at(self, plat: str, index: int) -> Optional[str]:
        plat = (plat or "").strip()
        if index < 0:
            return None
        if plat == FAILED_PLAT:
            if index >= len(self.failed):
                return None
            u = self.failed.pop(index)
            self.save()
            return u
        if plat not in PLATFORMS:
            return None
        n = 0
        for i, (p, u) in enumerate(self.items):
            if p != plat:
                continue
            if n == index:
                self.items.pop(i)
                self._rebuild_queues()
                self.save()
                return u
            n += 1
        return None

    def clear_platform(self, plat: str) -> int:
        plat = (plat or "").strip()
        if plat == FAILED_PLAT:
            n = len(self.failed)
            if n:
                self.failed = []
                self.save()
            return n
        before = len(self.items)
        self.items = [(p, u) for p, u in self.items if p != plat]
        n = before - len(self.items)
        if n:
            self._rebuild_queues()
            self.save()
        return n

    def pop_next(self) -> Optional[Tuple[str, str]]:
        """FIFO 弹出队头（兼容旧调用；预下载开跑请用 peek_next + remove_match）。"""
        item = self.peek_next()
        if not item:
            return None
        plat, line = item
        self.remove_match(plat, line)
        return plat, line

    def peek_next(self) -> Optional[Tuple[str, str]]:
        """只看下一条约，不删；下载成功后再 remove_match。顺序以 items 为准。"""
        if self.items:
            return self.items[0]
        return None

    def remove_match(self, plat: str, text: str) -> bool:
        """删除指定平台队列中第一条匹配行（规范化后比较）。成功返回 True。"""
        plat = (plat or "").strip()
        line = normalize_line(text)
        if plat not in PLATFORMS or not line:
            return False
        for i, (p, u) in enumerate(self.items):
            if p == plat and u == line:
                self.items.pop(i)
                self._rebuild_queues()
                self.save()
                return True
        return False

    def rotate_front(self, plat: str) -> bool:
        """把当前队头挪到总队尾（兼容旧调用；失败请用 move_to_failed）。"""
        plat = (plat or "").strip()
        if len(self.items) < 2:
            return False
        if self.items[0][0] != plat:
            return False
        self.items.append(self.items.pop(0))
        self._rebuild_queues()
        self.save()
        return True

    def move_to_failed(self, plat: str, text: str) -> bool:
        """解析失败 / 无法下载：移出自动队列，写入「无法处理」。"""
        plat = (plat or "").strip()
        line = normalize_line(text)
        if not line:
            return False
        new_items: List[Tuple[str, str]] = []
        removed = False
        for p, u in self.items:
            if not removed and u == line and (not plat or p == plat):
                removed = True
                continue
            new_items.append((p, u))
        self.items = new_items
        if line not in self.failed:
            self.failed.append(line)
        self._rebuild_queues()
        self.save()
        return True

    def can_move_to_end(self) -> bool:
        """整表至少 2 条才能把当前条排到最后。"""
        return len(self.items) > 1

    def move_to_end(self, plat: str, text: str) -> bool:
        """用户取消：把该条挪到 items / 对应平台队列的最后一位，立刻落盘。"""
        plat = (plat or "").strip()
        line = normalize_line(text)
        if plat not in PLATFORMS or not line:
            return False
        if len(self.items) <= 1:
            return False
        idx = None
        for i, (p, u) in enumerate(self.items):
            if p == plat and u == line:
                idx = i
                break
        if idx is None:
            return False
        item = self.items.pop(idx)
        self.items.append(item)
        self._rebuild_queues()
        self.save()
        return True

    def mark_finished(self) -> None:
        self.last_finish_ts = time.time()
        self.save()

    def cooldown_remaining(self) -> float:
        if self.last_finish_ts <= 0:
            return 0.0
        left = COOLDOWN_SEC - (time.time() - self.last_finish_ts)
        return max(0.0, left)

    def can_activate(self) -> bool:
        return self.cooldown_remaining() <= 0 and self.total_count() > 0
