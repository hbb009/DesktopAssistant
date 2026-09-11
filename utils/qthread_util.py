# utils/qthread_util.py
# 关窗时安全停止 QThread，避免 "QThread: Destroyed while thread is still running"

from __future__ import annotations

from typing import Any, Iterable

from utils.logger import get_logger

log = get_logger("utils.qthread_util")


def stop_qthread(
    worker,
    *,
    cancel_flag=None,
    timeout_ms: int = 2500,
    name: str = "",
) -> None:
    """停止一个 QThread：先置取消标志 / requestInterruption，再 quit+wait。

    - cancel_flag: 可选 list，约定 [bool]，下载/解析 worker 常用
    - 超时后 terminate（最后手段，仅关窗场景）
    """
    if cancel_flag is not None:
        try:
            cancel_flag[0] = True
        except Exception:
            pass
    if worker is None:
        return
    try:
        if not worker.isRunning():
            return
    except Exception:
        return

    label = name or type(worker).__name__
    try:
        # 若 worker 自带 stop()（如批量打标）
        stop_fn = getattr(worker, "stop", None)
        if callable(stop_fn):
            try:
                stop_fn()
            except Exception:
                pass
        try:
            worker.requestInterruption()
        except Exception:
            pass
        try:
            worker.quit()
        except Exception:
            pass
        if not worker.wait(int(timeout_ms)):
            log.warning("线程 %s 未在 %sms 内退出，terminate", label, timeout_ms)
            try:
                worker.terminate()
            except Exception:
                pass
            try:
                worker.wait(800)
            except Exception:
                pass
    except Exception:
        log.exception("停止线程 %s 异常", label)


def stop_qthreads(
    workers: Iterable[Any],
    *,
    cancel_flag=None,
    timeout_ms: int = 2500,
) -> None:
    """批量停止；cancel_flag 只置一次（共享标志时）。"""
    if cancel_flag is not None:
        try:
            cancel_flag[0] = True
        except Exception:
            pass
    for w in workers or []:
        stop_qthread(w, cancel_flag=None, timeout_ms=timeout_ms)


def abandon_page_workers(page) -> bool:
    """立刻让出下载页：置取消、换新取消标志、摘掉 worker，不等线程退出。

    旧线程仍拿着旧的 cancel 列表，会自己停；新任务用新的 [False]，
    不会把旧线程的取消拨回去。返回当时是否有线程在跑。
    """
    if page is None:
        return False
    busy = False
    for attr in ("_parse_worker", "_dl_worker", "_worker"):
        w = getattr(page, attr, None)
        try:
            if w is not None and w.isRunning():
                busy = True
                break
        except Exception:
            pass

    for attr in ("_cancel", "_cancel_flag"):
        flag = getattr(page, attr, None)
        if isinstance(flag, list) and flag:
            flag[0] = True
            setattr(page, attr, [False])

    kept = []
    for w in list(getattr(page, "_abandoned_workers", None) or []):
        try:
            if w.isRunning():
                kept.append(w)
        except Exception:
            pass
    for attr in ("_parse_worker", "_dl_worker", "_worker"):
        w = getattr(page, attr, None)
        try:
            setattr(page, attr, None)
        except Exception:
            pass
        if w is None:
            continue
        try:
            w.blockSignals(True)
        except Exception:
            pass
        try:
            running = bool(w.isRunning())
        except Exception:
            running = False
        if running:
            try:
                w.requestInterruption()
            except Exception:
                pass
            try:
                w.quit()
            except Exception:
                pass
            kept.append(w)
    try:
        page._abandoned_workers = kept
    except Exception:
        pass

    emit = getattr(page, "_emit_nav_progress", None)
    if callable(emit):
        try:
            emit(False)
        except Exception:
            pass
    return busy
