# utils/logger.py
# ---------------------------------------------------------------------------
# 全站日志：写到 data/app.log（滚动备份），方便用户反馈与本地排错。
# 用法：
#   from utils.logger import get_logger
#   log = get_logger(__name__)
#   log.info("启动")
#   log.exception("出错")   # 在 except 块内，会带堆栈
# ---------------------------------------------------------------------------

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Optional

_LOG_RETENTION_HOURS = 24.0
_CRASH_KEEP_HOURS = 2.0
_CRASH_KEEP_LAST = 2
_CRASH_NAME_LEN = len("crash_YYYYMMDD_HHMMSS.log")

_CONFIGURED = False
_LOG_DIR: Optional[str] = None


def _records_dir() -> str:
    global _LOG_DIR
    if _LOG_DIR:
        return _LOG_DIR
    try:
        from utils.app_paths import data_dir
        d = data_dir()
    except Exception:
        # 兜底：utils 上一级 / data
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        d = os.path.join(root, "data")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            d = root
    _LOG_DIR = d
    return d


def _purge_old_logs(max_age_hours: float = _LOG_RETENTION_HOURS) -> None:
    """删除超过 max_age_hours 的滚动备份日志（app.log.1 / .2 / ...）。

    当前 app.log 正在写入，不动；备份按文件修改时间判断，超龄即删。
    这样 data/ 里最多只有最近一天的有效日志，不会越堆越多分不清。
    """
    try:
        d = _records_dir()
        cutoff = time.time() - float(max_age_hours or _LOG_RETENTION_HOURS) * 3600
        removed = 0
        for name in os.listdir(d):
            if not name.startswith("app.log."):
                continue
            p = os.path.join(d, name)
            try:
                if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                    os.remove(p)
                    removed += 1
            except Exception:
                continue
        if removed:
            root = logging.getLogger("deskassist")
            root.info("已清理 %s 小时前的滚动日志 %s 个", int(max_age_hours), removed)
    except Exception:
        pass


def _crash_log_stamp(name: str, path: str) -> float:
    """启动时刻优先取文件名里的时间戳；解析失败再退回 mtime。"""
    if (
        name.startswith("crash_")
        and name.endswith(".log")
        and len(name) == _CRASH_NAME_LEN
    ):
        try:
            return datetime.strptime(name[6:-4], "%Y%m%d_%H%M%S").timestamp()
        except ValueError:
            pass
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def purge_old_crash_logs(
    crash_dir: Optional[str] = None,
    keep_path: str = "",
    keep_hours: float = _CRASH_KEEP_HOURS,
    keep_last: int = _CRASH_KEEP_LAST,
) -> int:
    """启动时清理 crash_*.log。

    空文件 = 每次正常启动都会预建、但从未真正崩溃的占位，除本次正在
    写的 keep_path 之外一律删掉；有内容（真崩溃栈）的才按老策略保留：
    最近 keep_hours 小时，或最近 keep_last 次启动（并集）。
    keep_path 是本次正在写入的文件，始终保留。返回删除个数。
    """
    try:
        d = crash_dir or _records_dir()
        names = [
            n for n in os.listdir(d)
            if n.startswith("crash_") and n.endswith(".log")
        ]
    except OSError:
        return 0

    now = time.time()
    cutoff = now - float(keep_hours or _CRASH_KEEP_HOURS) * 3600
    last_n = max(0, int(keep_last if keep_last is not None else _CRASH_KEEP_LAST))
    keep_abs = ""
    if keep_path:
        keep_abs = os.path.normcase(os.path.abspath(keep_path))

    items = []
    for name in names:
        p = os.path.join(d, name)
        if not os.path.isfile(p):
            continue
        items.append((_crash_log_stamp(name, p), p))
    items.sort(key=lambda x: x[0], reverse=True)

    keep = set()
    if keep_abs:
        keep.add(keep_abs)
    # 空占位文件除本次外全删；真崩溃栈才参与 2 小时 / 最近几次 的保留
    real_cnt = 0
    for stamp, p in items:
        if os.path.normcase(os.path.abspath(p)) in keep:
            continue
        empty = True
        try:
            empty = os.path.getsize(p) <= 0
        except OSError:
            empty = True
        if empty:
            continue
        if real_cnt < last_n or stamp >= cutoff:
            keep.add(os.path.normcase(os.path.abspath(p)))
            real_cnt += 1

    removed = 0
    for _stamp, p in items:
        if os.path.normcase(os.path.abspath(p)) in keep:
            continue
        try:
            os.remove(p)
            removed += 1
        except Exception:
            continue
    return removed


def _ensure_root_configured() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    _purge_old_logs()

    root = logging.getLogger("deskassist")
    root.setLevel(logging.DEBUG)
    # 避免重复 handler（热重载 / 多次 import）
    if root.handlers:
        return

    log_path = os.path.join(_records_dir(), "app.log")
    try:
        fh = RotatingFileHandler(
            log_path,
            maxBytes=2_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d — %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        root.addHandler(fh)
    except Exception:
        # 写盘失败时至少不拖垮启动
        sh = logging.StreamHandler()
        sh.setLevel(logging.WARNING)
        sh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
        ))
        root.addHandler(sh)

    # 降噪第三方库
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


def get_logger(name: str = "deskassist") -> logging.Logger:
    """返回命名 logger。name 建议传 __name__。"""
    _ensure_root_configured()
    if not name or name == "deskassist":
        return logging.getLogger("deskassist")
    # 统一挂到 deskassist 树下，便于过滤
    if name.startswith("deskassist"):
        return logging.getLogger(name)
    return logging.getLogger(f"deskassist.{name}")


def ignored(logger: logging.Logger, msg: str, *args) -> None:
    """except 里选择吞掉时调用：不改变容错，只把堆栈写到 debug。"""
    logger.debug(msg, *args, exc_info=True)


def log_path() -> str:
    """当前日志文件路径（供关于页/排错说明引用）。"""
    return os.path.join(_records_dir(), "app.log")
