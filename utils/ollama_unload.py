# utils/ollama_unload.py
# 「卸载模型」：只释放本机 Ollama 里已加载模型占用的内存/显存。
#   用 Ollama 官方 HTTP 接口：GET /api/ps 看哪些模型已加载；
#   POST /api/generate {"model": 名字, "keep_alive": 0} 让该模型立刻卸载（等价于 ollama stop 名字）。
#   重新加载：POST /api/generate {"model": 名字}（不带 prompt）——官方的「预加载模型」方式。
#   不停 Ollama 服务、不杀任何进程、不碰对话记录和文件。由「新对话」在后台调用。

from __future__ import annotations

import time
from typing import Callable, Optional

import requests

from utils.logger import get_logger

log = get_logger("deskassist.ollama")


def _get_json(base: str, path: str, payload: Optional[dict] = None, timeout: float = 5.0):
    url = base.rstrip("/") + path
    if payload is None:
        r = requests.get(url, timeout=timeout)
    else:
        r = requests.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return {}


def loaded_models(base: str, timeout: float = 5.0, fetch: Callable = _get_json) -> list:
    """当前已加载在内存/显存里的模型名（/api/ps）。"""
    d = fetch(base, "/api/ps", None, timeout) or {}
    names = []
    for m in d.get("models") or []:
        name = str(m.get("name") or m.get("model") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _same_model(a: str, b: str) -> bool:
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    tag = lambda s: s if ":" in s.split("/")[-1] else s + ":latest"
    return tag(a) == tag(b)


def pick_targets(current: str, loaded: list) -> list:
    """优先只卸当前聊天用的模型；它没加载时，才卸其他已加载的模型。"""
    for name in loaded or []:
        if _same_model(current, name):
            return [name]
    return list(loaded or [])


def unload_models(
    base: str,
    current: str,
    fetch: Callable = _get_json,
    wait_s: float = 5.0,
    sleep: Callable = time.sleep,
) -> dict:
    """卸载并核对。返回 {"ok": bool, "text": 状态文字, "unloaded": [...], "left": [...]}。"""
    try:
        loaded = loaded_models(base, fetch=fetch)
    except Exception as e:
        log.info("ollama 卸载：查询已加载模型失败：%s", e)
        return {"ok": False, "text": f"没能连上 Ollama，无法查看已加载的模型：{e}", "unloaded": [], "left": []}
    if not loaded:
        return {"ok": False, "text": "当前没有已加载的模型，不需要卸载。", "unloaded": [], "left": []}
    targets = pick_targets(current, loaded)
    errors = []
    for name in targets:
        try:
            fetch(base, "/api/generate", {"model": name, "keep_alive": 0, "stream": False}, 60.0)
            log.info("ollama 卸载模型 已请求 %s", name)
        except Exception as e:
            log.info("ollama 卸载模型 失败 %s：%s", name, e)
            errors.append(f"{name}（{e}）")
    # 以 /api/ps 为准核对，不凭请求返回就报成功。
    left = list(targets)
    deadline = max(0.0, float(wait_s))
    waited = 0.0
    while True:
        try:
            now = loaded_models(base, fetch=fetch)
        except Exception:
            now = None
        if now is not None:
            left = [t for t in targets if any(_same_model(t, n) for n in now)]
            if not left:
                break
        if waited >= deadline:
            break
        sleep(0.5)
        waited += 0.5
    done = [t for t in targets if t not in left]
    if done and not left and not errors:
        return {"ok": True, "text": "已卸载模型：" + "、".join(done) + "。内存/显存已释放，对话记录没有变。",
                "unloaded": done, "left": []}
    parts = []
    if done:
        parts.append("已卸载：" + "、".join(done))
    if left:
        parts.append("仍在内存里：" + "、".join(left))
    if errors:
        parts.append("请求失败：" + "；".join(errors))
    return {"ok": False, "text": "卸载没有完成。" + "；".join(parts) + "。", "unloaded": done, "left": left}


def _is_loaded(base: str, name: str, fetch: Callable) -> Optional[bool]:
    try:
        now = loaded_models(base, fetch=fetch)
    except Exception:
        return None
    return any(_same_model(name, n) for n in now)


def reload_model(
    base: str,
    current: str,
    fetch: Callable = _get_json,
    wait_s: float = 5.0,
    sleep: Callable = time.sleep,
) -> dict:
    """新对话用：先卸载当前模型（释放内存/显存和 KV 缓存），再把同一个模型加载回来。

    只动当前聊天用的这个模型，不碰 Ollama 里其他已加载的模型。
    返回 {"ok": bool, "text": 状态文字, "unloaded": bool, "loaded": bool}。
    """
    name = (current or "").strip()
    if not name:
        return {"ok": False, "text": "还没有选模型，没有重新加载。", "unloaded": False, "loaded": False}
    try:
        loaded = loaded_models(base, fetch=fetch)
    except Exception as e:
        log.info("ollama 重新加载：查询已加载模型失败：%s", e)
        return {"ok": False, "text": f"没能连上 Ollama，模型没有重新加载：{e}", "unloaded": False, "loaded": False}
    target = next((n for n in loaded if _same_model(name, n)), "")
    unloaded = False
    if target:
        try:
            fetch(base, "/api/generate", {"model": target, "keep_alive": 0, "stream": False}, 60.0)
            log.info("ollama 新对话 卸载模型 %s", target)
        except Exception as e:
            log.info("ollama 新对话 卸载失败 %s：%s", target, e)
            return {"ok": False, "text": f"卸载模型没有完成：{e}", "unloaded": False, "loaded": True}
        waited = 0.0
        while True:
            state = _is_loaded(base, target, fetch)
            if state is False:
                unloaded = True
                break
            if waited >= max(0.0, float(wait_s)):
                break
            sleep(0.5)
            waited += 0.5
        if not unloaded:
            return {"ok": False, "text": f"卸载模型没有完成：{target} 仍在内存里。", "unloaded": False, "loaded": True}
    load_name = target or name
    try:
        fetch(base, "/api/generate", {"model": load_name, "stream": False}, 600.0)
        log.info("ollama 新对话 重新加载模型 %s", load_name)
    except Exception as e:
        log.info("ollama 新对话 加载失败 %s：%s", load_name, e)
        head = "已卸载，但" if unloaded else ""
        return {"ok": False, "text": f"{head}重新加载模型没有完成：{e}", "unloaded": unloaded, "loaded": False}
    if _is_loaded(base, load_name, fetch) is False:
        head = "已卸载，但" if unloaded else ""
        return {"ok": False, "text": f"{head}重新加载模型没有完成：{load_name} 没有出现在已加载列表里。",
                "unloaded": unloaded, "loaded": False}
    if unloaded:
        text = f"模型已重新加载：{load_name}（内存/显存已释放后重新载入）。"
    else:
        text = f"模型已加载：{load_name}（之前没有加载，无需卸载）。"
    return {"ok": True, "text": text, "unloaded": unloaded, "loaded": True}
