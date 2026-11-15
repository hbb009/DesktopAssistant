# utils/ollama_agent.py
# 借鉴 Qwen-Agent fncall_agent：
#   while 还有额度:
#       调模型（带工具）
#       检测到 tool_call → 程序执行 → 用原生 role="tool" 把结果写回对话
#       没有 tool_call → 结束
# 针对本地 30B+ GGUF 模型（如 qwen3.6-jailbreak:q6, 30720 上下文）特别优化：
#   1. 工具返回采用原生 role="tool"，匹配 Qwen3 / Llama3 微调模板，杜绝把工具返回伪装成 user 导致模型精神分裂；
#   2. 平时不篡改历史消息，100% 保护 llamacpp KV Cache 前缀命中；仅在超过 26000 字时压缩旧工具正文；
#   3. 兼容自定义 GGUF 模型在正文中输出 JSON / <tool_call> / 代码块，以及剥离混入正文的 <think>...</think> 块。

from __future__ import annotations

import json
import os
import re
import threading
from typing import Callable, Iterator, Optional

from utils.logger import get_logger

log = get_logger("deskassist.ollama")

# 失控循环的最后栏杆，不是日常控制手段，也不交给模型去判断。
MAX_LLM_CALL_PER_RUN = 2000
# 程序替模型把目录章节存完时，用这个开头结束循环。模型不再逐章收尾。
CATALOG_DONE_MARK = "【目录保存结果】"
# 模型口头那十来个字不能当正文。网页抓取结果走缓存，不走回复。
_MIN_SPOKEN_SAVE = 200
# 适配 30720 上下文窗口：累积超过 26000 字（约 1.7 万 token）时才压缩较早的工具正文，平时保持前缀不变以命中 KV Cache。
COMPACT_THREAD_CHARS = 26000

AGENT_SYSTEM = (
    "你是带工具的本地助手，请用简体中文回答，直接、准确地执行用户的指令。\n"
    "【核心工具使用规则】\n"
    "1. 查看/读取文件：调用 list_articles 查看目录清单；调用 read_article 读取指定文件（优先传 index=清单编号，或原样复制清单中的完整相对路径 filename，不要拆分文件名里的空格或标点）。若返回提示还有未读部分，带上提示中的 start=数字 继续读取。\n"
    "2. 保存/写入文件：调用 save_article，必须同时传 filename 相对路径和 content 正文。不要只传文件名或用口头说明代替正文。写长文分段：第一段 mode=\"overwrite\"，后续同文件名 mode=\"append\"。\n"
    "3. 整理/重命名文件：若用户只要求修改或整理文件名（不要求改内容），只调用 list_articles 和 rename_article（可用 mode=\"han_digits\" 或 renames 列表），不要调用 read_article 或 save_article。\n"
    "4. 网页搜索与抓取：需要联网搜索时调用 web_search；打开具体网址时调用 web_fetch。保存已抓取的网页正文时，save_article 同时传 filename、url 和 content=空字符串，程序会使用网页缓存。只有这种情况允许空 content。\n"
    "5. 真实性原则：只有工具返回「已保存：路径」才代表文件已落盘，绝不要在未调用工具或保存失败时口头声称「已保存」。\n"
    "长任务：每读一个片段先用 save_notes 保存分析和续读位置，再读下一段。写长文件每次 content 不超过1000字，首段 overwrite，后续 append，中间段 complete=false，最后一段 complete=true。\n"
    "6. 推进与收尾：请对照【任务卡】逐步推进，不要循环重读未改动的文件；当成功标准满足后直接给出简短结论并结束，不再调用工具。"
)

_LOCAL = threading.local()
_DIGEST_TAG = "【已有进度，由程序记录】"
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)
_THINK_UNCLOSED_RE = re.compile(r"<think>.*$", re.S | re.I)
_CODE_BLOCK_RE = re.compile(r"```(?:markdown|md|txt|text)?\s*\n(.*?)```", re.S | re.I)
_ACK_SHORT_RE = re.compile(
    r"^(?:已保存|正在保存|先保存|好的|收到|明白|我来保存|下面保存|开始保存|已为您保存|文件已保存)[^。\n]{0,30}[。！!]?$"
)


def strip_think_blocks(text: str) -> str:
    """剥离自定义 GGUF 模型混在 content 中的 <think>...</think> 思考块。"""
    if not text or "<think>" not in text.lower():
        return text or ""
    cleaned = _THINK_BLOCK_RE.sub("", text)
    if "<think>" in cleaned.lower():
        cleaned = _THINK_UNCLOSED_RE.sub("", cleaned)
    return cleaned.strip()


def clear_fetched() -> None:
    _LOCAL.pages = []


def remember_fetch(url: str, title: str, text: str) -> None:
    pages = getattr(_LOCAL, "pages", None)
    if pages is None:
        pages = []
        _LOCAL.pages = pages
    body = (text or "").strip()
    cut = body.find("\n\n页面上的链接：")
    if cut > 0:
        body = body[:cut].strip()
    if not body:
        return
    item = {"url": (url or "").rstrip("/"), "title": (title or "").strip(), "text": body}
    pages.append(item)
    log.info("ollama 缓存网页正文 title=%s chars=%d url=%s", item["title"][:40], len(body), item["url"])


def pick_save_text(args: dict, spoken: str = "") -> str:
    """保存正文：优先显式 content，其次 web_fetch 缓存，再次回复中的代码块或够长的正文。口头短句一律不用。"""
    args = args or {}
    explicit = strip_think_blocks(str(args.get("content") or args.get("text") or args.get("contents") or "")).strip()
    if len(explicit) >= _MIN_SPOKEN_SAVE:
        return explicit + "\n"
    url = str(args.get("url") or "").strip().rstrip("/")
    fn = str(args.get("filename") or args.get("name") or args.get("path") or "").strip()
    stem = os.path.splitext(os.path.basename(fn.replace("\\", "/")))[0]
    pages = list(getattr(_LOCAL, "pages", None) or [])
    if url:
        for item in reversed(pages):
            if item["url"] == url or item["url"].endswith(url) or url.endswith(item["url"]):
                return item["text"] + "\n"
    if stem:
        key = re.sub(r"\s+", "", stem)
        for item in reversed(pages):
            title = re.sub(r"\s+", "", item.get("title") or "")
            if key and title and (key in title or title in key):
                return item["text"] + "\n"
    # 只缓存了一页时可以用它。多页时对不上文件名就不要拿最后一页，否则会把上一章写进新文件。
    if len(pages) == 1 and len(pages[0].get("text") or "") >= _MIN_SPOKEN_SAVE:
        return pages[0]["text"] + "\n"
    # 若显式传入了非空的短正文（且不是「同上」占位词），也允许保存
    if explicit and explicit not in {"同上", "见上文", "上文", "正文", "（同上）", "(同上)", "同上文", "回复正文"} and len(explicit) >= 20:
        return explicit + "\n"
    cleaned_spoken = strip_think_blocks(spoken or "").strip()
    if cleaned_spoken:
        # 若回复中包含 ```markdown ... ``` 代码块，优先提取最长代码块作为文件正文
        blocks = [m.group(1).strip() for m in _CODE_BLOCK_RE.finditer(cleaned_spoken) if len(m.group(1).strip()) >= 40]
        if blocks:
            best_block = max(blocks, key=len)
            # 排除工具调用 JSON 代码块
            if not (best_block.startswith("{") and '"name"' in best_block[:120]):
                return best_block + "\n"
    if len(cleaned_spoken) >= _MIN_SPOKEN_SAVE and not _ACK_SHORT_RE.match(cleaned_spoken):
        return cleaned_spoken + "\n"
    return ""


_BAD_ARGS_RE = re.compile(r"invalid tool call arguments|tool call arguments|unexpected end of json|error parsing tool call", re.I)
_BAD_ARGS_NAME_RE = re.compile(r'for\s+"([A-Za-z_][\w-]*)"')
BAD_ARGS_RETRIES = 3


def bad_tool_args_error(exc) -> bool:
    """模型服务拒收了模型生成的工具参数（JSON 不完整）。这不是「模型不支持工具」。"""
    return bool(_BAD_ARGS_RE.search(str(exc or "")))


def _bad_args_hint(exc) -> str:
    m = _BAD_ARGS_NAME_RE.search(str(exc or ""))
    name = m.group(1) if m else "工具"
    text = (
        f"（程序提示）上一次调用 {name} 时生成的 JSON 参数不完整，已被拒收。"
        "请分段调用该工具：save_article 每次 content 不超过 1000 字，第一段 overwrite，后续 append，中间段 complete=false，最后一段 complete=true。"
        "不要重发原来过长的 JSON；未成功的这一段需要重新保存。"
    )
    if name == "save_article" or name == "工具":
        text += (
            "如果是调用 save_article 保存长文，为避免 JSON 参数过长断裂，你可以：\n"
            "把这一段实际正文放进 save_article 的 content 参数，不能只传 filename；\n"
            "方式2：分段保存，第一段 mode=\"overwrite\"，后续用同一 filename 加 mode=\"append\"（每段 1500 字以内）。"
        )
    return text


_PSEUDO_KEYS = {
    "file", "filename", "path", "action", "index", "start", "tool", "name",
    "arguments", "parameters", "args", "params", "content", "text", "url",
    "query", "q", "mode", "new_filename", "new_name", "renames", "recursive", "files",
}
_PSEUDO_ACTIONS = {
    "read": "read_article", "read_article": "read_article", "open": "read_article",
    "list": "list_articles", "list_articles": "list_articles", "ls": "list_articles",
    "save": "save_article", "save_article": "save_article", "write": "save_article",
    "rename": "rename_article", "rename_article": "rename_article",
    "save_notes": "save_notes", "note": "save_notes",
    "web_search": "web_search", "search": "web_search",
    "web_fetch": "web_fetch", "fetch": "web_fetch",
    "read_unread_batch": "read_unread_batch",
}
_FENCE_RE = re.compile(r"```(?:json|tool_call|tool)?\s*([\[{].*?[\]}])\s*```", re.S | re.I)


def pseudo_tool_calls(spoken: str, tool_names) -> list:
    """兼容自定义 GGUF 模型把工具调用写成正文里的 JSON（如 {"name": "...", "arguments": {...}} 或简写格式）。"""
    text = strip_think_blocks(spoken or "").strip()
    names = set(tool_names or [])
    if not text or not names:
        return []
    blobs = [m.group(1) for m in _FENCE_RE.finditer(text)]
    if not blobs:
        if len(text) > 1600:
            return []
        s = text.find("["), text.find("{")
        starts = [x for x in s if x >= 0]
        if not starts:
            return []
        blobs = [text[min(starts):]]
    out = []
    dec = json.JSONDecoder()
    for blob in blobs:
        try:
            obj, _end = dec.raw_decode(blob.strip())
        except Exception:
            continue
        items = obj if isinstance(obj, list) else [obj]
        for it in items:
            if not isinstance(it, dict) or not it or set(it) - _PSEUDO_KEYS:
                continue
            act = str(it.get("action") or it.get("tool") or it.get("name") or "").strip().lower()
            tool = _PSEUDO_ACTIONS.get(act)
            if not tool or tool not in names:
                continue
            # 支持标准 {"name": "...", "arguments": {...}} 结构
            nested = it.get("arguments") if isinstance(it.get("arguments"), dict) else (
                it.get("parameters") if isinstance(it.get("parameters"), dict) else (
                    it.get("args") if isinstance(it.get("args"), dict) else None
                )
            )
            src = dict(nested) if nested is not None else dict(it)
            src.pop("action", None)
            src.pop("tool", None)
            if nested is None:
                src.pop("name", None)
            if tool == "read_article":
                args = {}
                fn = str(src.get("filename") or src.get("file") or src.get("path") or "").strip()
                if fn:
                    args["filename"] = fn
                if src.get("index") not in (None, ""):
                    args["index"] = src["index"]
                if src.get("start") not in (None, ""):
                    args["start"] = src["start"]
                if not args:
                    continue
            elif tool == "list_articles":
                args = {}
                if src.get("path"):
                    args["path"] = str(src["path"]).strip()
                if src.get("index") not in (None, ""):
                    args["index"] = src["index"]
                if src.get("recursive") is not None:
                    args["recursive"] = src["recursive"]
            else:
                args = {k: v for k, v in src.items() if v is not None}
            out.append({"type": "function", "function": {"name": tool, "arguments": args}})
    return out[:4]


def _tool_names(tools) -> list:
    names = []
    for t in tools or []:
        fn = (t or {}).get("function") if isinstance(t, dict) else None
        if isinstance(fn, dict) and fn.get("name"):
            names.append(str(fn["name"]))
    return names


def saved_count_changed(saved: list, progress) -> bool:
    """这一轮有没有新保存产出（save_article 成功）。用已保存条数变化判断。"""
    n = len(saved or [])
    last = getattr(progress, "_saved_seen", 0)
    try:
        progress._saved_seen = n
    except Exception:
        pass
    return n > last


def _thread_chars(thread: list) -> int:
    total = 0
    for msg in thread or []:
        if isinstance(msg, dict):
            total += len(str(msg.get("content") or ""))
    return total


def _is_toolish(msg: dict) -> bool:
    if not isinstance(msg, dict):
        return False
    if msg.get("role") == "tool":
        return True
    if msg.get("role") != "user":
        return False
    text = str(msg.get("content") or "")
    return any(
        k in text
        for k in (
            "【工具",
            "已打开网页",
            "【程序自动读取",
            "【自动读取",
            "【批次文件：",
            "【本次读取的文件：",
        )
    )


def _ensure_digest(thread: list, digest: str) -> None:
    if not digest:
        return
    body = _DIGEST_TAG + "\n" + digest + "\n请从进度接着做，不要重复读取已完成且未改动的文件。"
    for msg in reversed(thread[-6:] if thread else []):
        if not isinstance(msg, dict):
            continue
        text = str(msg.get("content") or "")
        if _DIGEST_TAG in text and msg.get("role") == "user":
            if "【工具" not in text and "【程序自动读取" not in text and "【自动读取" not in text:
                msg["content"] = body
            return
    thread.append({"role": "user", "content": body})


def _files_of(text: str) -> list:
    try:
        from utils.ollama_progress import files_in_tool_text
        return files_in_tool_text(text)
    except Exception:
        return re.findall(r"【自动读取 ([^\s\]]+)", text or "")


def _compact_old_tools(thread: list, digest: str = "", aggressive: bool = False, progress=None) -> None:
    """仅在上下文接近上限时压缩较早的工具返回正文，平时绝不改动历史消息以保证 llamacpp KV Cache 100% 命中。"""
    if not thread:
        return
    # Written content is durable. Keep the tool-call protocol, but avoid sending
    # every saved draft and note again. Never shorten failed/unexecuted writes.
    for pos, msg in enumerate(thread[:-2]):
        if msg.get("role") != "assistant" or not msg.get("tool_calls"):
            continue
        results = []
        for following in thread[pos + 1:]:
            if following.get("role") != "tool":
                break
            results.append(following)
        msg["tool_calls"] = json.loads(json.dumps(msg["tool_calls"], ensure_ascii=False))
        for call, result in zip(msg["tool_calls"], results):
            fn = call.get("function") or {}
            name = fn.get("name")
            prefix = {"save_notes": "已记下笔记", "save_article": "已保存："}.get(name)
            args = fn.get("arguments")
            if prefix and result.get("name") == name and str(result.get("content") or "").startswith(prefix) and isinstance(args, dict):
                if len(str(args.get("content") or "")) > 200:
                    args["content"] = "（正文已持久化；笔记可用 read_notes 分页读取，成果可用 read_article 读取。）"
    idxs = [i for i, msg in enumerate(thread) if _is_toolish(msg)]
    keep = 1 if aggressive else 3
    compact_upto = idxs[:-keep] if len(idxs) > keep else []
    if aggressive and idxs:
        compact_upto = idxs[:-1]
    for i in compact_upto:
        text = str(thread[i].get("content") or "")
        if len(text) < 600 and "已缩短" in text:
            continue
        files = _files_of(text)
        if progress is not None and files and not progress.can_compact_files(files, strict=False):
            continue
        names = files or re.findall(r"【自动读取 ([^\s\]]+)", text)
        if names:
            stub = "【较早读取的文件正文已收起以节省上下文：" + "、".join(names[:20]) + "。要点已记入进度记录。】"
        else:
            stub = text[:360].rstrip() + "\n…（较早的工具返回已收起以节省上下文。）"
        thread[i]["content"] = stub
    if digest and aggressive:
        _ensure_digest(thread, digest)


_FAIL_PREFIX = (
    "没有保存",
    "没有完成",
    "没有这个工具",
    "打开网页失败",
    "只能打开",
    "搜索失败",
    "已拒绝覆盖",
    "文章是空的",
    "文章太长",
    "文件不能放到",
    "这个名字已经是文件夹",
    "找不到这篇文章",
    "只能填写文件名",
    "文件名不合法",
    "文件名不能",
    "文件名是空的",
    "没有读成",
    "没有权限读取",
    "这不是文本文章",
    "只能阅读",
    "这是文件夹",
    "没有这个编号",
    "还没有目录清单",
    "index 必须是",
    "请提供 filename",
    "这是整理文件名的任务",
    "目标已存在，未改名",
    "没有改名",
    "新文件名为空",
    "这不是文件夹",
    "这个位置不能",
    "保存目录",
    "还没有指定",
    "只能查看",
    "没有看成",
    "没有权限查看",
    "找不到唯一",
    "文章目录没有开启",
    "没有进度记录",
    "只能保存",
    "已停止",
    "【请先写笔记】",
    "没有文件被",
)

STALL_STEER_ROUNDS = 3
STALL_STOP_ROUNDS = 8
_RECORD_HEADS = ("【已有记录】", "【不必再读】")
READ_ONLY_STEER_FILES = 10


def _read_failed(text: str) -> bool:
    """读取结果只看程序写的开头（每个文件块的第一行），不扫正文：小说里以「没有完成」开头的一行不是失败。"""
    check = True
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("----- ") and s.endswith(" -----"):
            check = True
            continue
        if s.startswith(("【本次读取的文件：", "按文件名在工作目录中匹配到：")):
            continue
        if check:
            if s.startswith(_FAIL_PREFIX):
                return True
            check = False
    return False


def _tool_failed(result: str, name: str = "") -> bool:
    if name in ("read_article", "read_unread_batch"):
        return _read_failed(result)
    text = result or ""
    for line in text.splitlines() or [""]:
        s = line.strip()
        if s.startswith("----- ") and s.endswith(" -----"):
            continue
        if s.startswith(_FAIL_PREFIX):
            return True
    return False


def _stuck_where(name: str, args: dict) -> str:
    args = args or {}
    fn = str(args.get("filename") or args.get("name") or args.get("path") or "").strip()
    url = str(args.get("url") or "").strip()
    query = str(args.get("query") or args.get("q") or "").strip()
    if name == "save_article" and fn:
        return f"保存 {fn}"
    if name == "rename_article":
        return f"重命名 {fn}" if fn else "重命名"
    if name == "save_notes":
        return "保存笔记"
    if name == "web_fetch" and url:
        return f"打开 {url[:80]}"
    if name == "web_search" and query:
        return f"搜索 {query[:40]}"
    if fn:
        return f"{name} {fn}"
    if url:
        return f"{name} {url[:80]}"
    return name or "工具"


def _context_stopped(exc: Exception) -> bool:
    s = str(exc or "").lower()
    return any(k in s for k in ("context length", "context window", "context limit", "上下文", "截断"))


def _resume_note(progress, saved: list) -> str:
    note = "\n进度已记下。请再发一条消息，程序会从记录接着做。"
    if progress is not None:
        try:
            line = progress.status_line()
            if line:
                note += "\n" + line
        except Exception:
            pass
    if saved:
        note += "\n已保存：" + "；".join(saved)
    return note


_CARD_TAG = "【任务卡】"
_OBS_TAG = "【观察】"


def _last_user_text(thread: list) -> str:
    for msg in reversed(thread or []):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        text = str(msg.get("content") or "").strip()
        if not text:
            continue
        if _DIGEST_TAG in text or _CARD_TAG in text or "【工具" in text or text.startswith("以下是程序执行"):
            continue
        return text
    return ""


def _target_of(name: str, args: dict) -> str:
    args = args or {}
    for key in ("filename", "name", "path", "url", "query", "q"):
        val = str(args.get(key) or "").strip()
        if val:
            return val
    return ""


def _exec_status(name: str, args: dict) -> str:
    target = _target_of(name, args)
    if name in ("read_article", "read_unread_batch"):
        return "读取 " + target if target else "读取"
    if name == "save_article":
        return "写入 " + target if target else "写入"
    if name == "rename_article":
        return "重命名 " + target if target else "重命名"
    if name == "save_notes":
        return "记录笔记"
    if name == "list_articles":
        return "查看目录"
    if name == "web_search":
        return "搜索 " + (target[:40] if target else "")
    if name == "web_fetch":
        return "打开页面"
    return name or "执行"


def format_observation(name: str, args: dict, result: str) -> str:
    """把工具返回格式化为清晰、紧凑的工具结果，必要时附上简短下一步提示。"""
    raw = result or ""
    head = raw.lstrip()
    nxt = ""
    if "没有传入正文" in head or (name == "save_article" and head.startswith("没有保存：没有传入正文")):
        nxt = "\n\n[程序提示] 保存失败：因为没有正文。请把该文件的完整正文放进 save_article 的 content 参数再保存，或先在回复里写出全文。"
    elif head.startswith("【已有记录】"):
        nxt = "\n\n[程序提示] 该文件已读过且未改动，请继续处理未完成的部分，不要循环重读。"
    elif "请再次调用" in raw or "还有未读部分" in raw or re.search(r"start\s*=\s*\d+", raw):
        nxt = "\n\n[程序提示] 文件尚未读完，请用同一 filename 并带上提示中的 start 数字继续读取。"
    elif name == "rename_article" or "请调用 rename_article" in head or head.startswith("这是整理文件名"):
        if "这是整理文件名" in head:
            nxt = "\n\n[程序提示] 请改用 rename_article，不要读取或另存正文。"
    return raw + nxt


def _stage_status(progress, stage: str, detail: str = "") -> str:
    if progress is not None:
        try:
            progress.set_stage(stage, detail)
        except Exception:
            pass
    if detail:
        return stage + " · " + detail
    return stage


def run_fncall(
    *,
    round_iter: Callable,
    exec_tool: Callable[[str, dict], str],
    messages: list,
    tools: list,
    should_stop: Optional[Callable[[], bool]] = None,
    on_status: Optional[Callable[[str], None]] = None,
    think: Optional[bool] = False,
    max_calls: int = MAX_LLM_CALL_PER_RUN,
    stop_review: Optional[Callable[[str], str]] = None,
    progress=None,
    read_chars: int = 0,
) -> Iterator[str]:
    """FnCall 循环。round_iter(messages, tools, sink, think) 产出模型正文。"""
    from utils.ollama_chat import _round_calls, _stopped

    thread = [dict(m) for m in messages if isinstance(m, dict)]
    yielded = False
    saved = []
    last_fail = None
    content_hint = None
    reviews_left = 3
    server_retries = 0
    n = max(1, int(max_calls or MAX_LLM_CALL_PER_RUN))
    ctx_retries = 0
    wrap_offered = False
    idle_rounds = 0
    last_mark = None
    bad_args = 0
    pseudo_used = set()
    done_at_output = 0
    read_steer_at = 0
    pending_note_files = set()
    repair_save_target = ""
    last_substantive_spoken = ""
    clear_fetched()

    if progress is not None:
        try:
            progress.ensure_task_card(_last_user_text(thread) or (progress.data.get("goal") or ""))
        except Exception:
            pass
        try:
            last_mark = progress.progress_mark()
        except Exception:
            last_mark = None
        if progress.is_fresh():
            fresh = progress.fresh_task_text()
            if fresh:
                # 附在最后一条 user 消息末尾，避免连发两条 user 消息破坏部分 GGUF 的聊天模板
                if thread and thread[-1].get("role") == "user":
                    thread[-1] = dict(thread[-1])
                    thread[-1]["content"] = str(thread[-1].get("content") or "").rstrip() + "\n\n" + fresh
                else:
                    thread.append({"role": "user", "content": fresh})
        elif progress.has_digest() or progress.task_card():
            _ensure_digest(thread, progress.digest())
        if progress.success_met():
            if on_status:
                on_status(_stage_status(progress, "已完成", "成功标准已满足"))
            yield "\n" + progress.success_stop_text()
            return

    for i in range(n):
        if _stopped(should_stop):
            if progress is not None:
                progress.save()
            return
        # 仅在上下文字符数超过阈值（26000字）时才压缩旧工具消息，平时绝不修改历史消息，保护 KV Cache！
        if _thread_chars(thread) > COMPACT_THREAD_CHARS:
            digest = progress.digest() if progress is not None else ""
            _compact_old_tools(thread, digest=digest, aggressive=True, progress=progress)
        if progress is not None:
            try:
                progress.set_stage("感知")
                progress.set_stage("规划")
            except Exception:
                pass
        if on_status:
            on_status("规划中")
        sink: dict = {}
        spoken_bits = []
        try:
            round_tools = tools
            if repair_save_target:
                round_tools = json.loads(json.dumps([t for t in tools if (t.get("function") or {}).get("name") == "save_article"], ensure_ascii=False))
                if round_tools:
                    params = round_tools[0]["function"]["parameters"]
                    params["required"] = ["filename", "content"]
                    params["properties"]["filename"]["enum"] = [repair_save_target]
                    params["properties"]["content"]["minLength"] = 1
                else:
                    round_tools = tools
            for piece in round_iter(thread, round_tools, sink, think):
                if piece:
                    spoken_bits.append(piece)
                    yielded = True
                    yield piece
        except Exception as e:
            log.info("ollama agent 中断：%s", e)
            if bad_tool_args_error(e):
                bad_args += 1
                if progress is not None:
                    progress.save()
                if bad_args <= BAD_ARGS_RETRIES:
                    thread.append({"role": "user", "content": _bad_args_hint(e)})
                    if on_status:
                        on_status("模型生成的工具参数不完整，已退回让它重新调用…")
                    continue
                yield (
                    "\n模型连续几次生成的工具参数都不完整（模型服务拒收），程序先停在这里，没有保存这一步的内容。"
                    "已做的步骤都已记下；发送「继续」会从记录接着做。"
                )
                return
            if _context_stopped(e):
                if progress is not None:
                    progress.save()
                    _compact_old_tools(thread, digest=progress.digest(), aggressive=True, progress=progress)
                    _ensure_digest(thread, progress.digest())
                    ctx_retries += 1
                    if ctx_retries <= 3:
                        if on_status:
                            on_status("上下文已满，已记下进度，压缩后继续…")
                        continue
                    yield "\n未完成：上下文恢复连续失败。" + _resume_note(progress, saved)
                    return
                if saved:
                    if on_status:
                        on_status(f"上下文已满，已保存 {len(saved)} 个文件。")
                    yield "\n上下文已满，这一轮先停。已保存：" + "；".join(saved)
                    return
            if re.search(r"HTTP (500|502|503|504)", str(e), re.I):
                server_retries += 1
                if server_retries <= 3:
                    if on_status:
                        on_status("模型服务暂时失败，正在重试（%d/3）…" % server_retries)
                    continue
                if progress is not None:
                    progress.save()
                yield "\n未完成：模型服务连续失败。原因：" + str(e) + _resume_note(progress, saved)
                return
            raise
        ctx_retries = 0
        server_retries = 0
        if _stopped(should_stop):
            if progress is not None:
                progress.save()
            return
        spoken = "".join(sink.get("content") or spoken_bits)
        cleaned_spoken = strip_think_blocks(spoken)
        if len(cleaned_spoken.strip()) >= _MIN_SPOKEN_SAVE:
            last_substantive_spoken = cleaned_spoken.strip()
        calls = _round_calls(sink)
        bad_args = 0
        if not calls and tools:
            pseudo = pseudo_tool_calls(spoken, _tool_names(tools))
            key = json.dumps(pseudo, ensure_ascii=False, sort_keys=True) if pseudo else ""
            if pseudo and key not in pseudo_used:
                pseudo_used.add(key)
                calls = pseudo
                log.info("ollama agent 正文 JSON 当作工具调用 %s", key[:200])
                if on_status:
                    on_status("识别到正文中的工具调用，正在执行…")
        log.info(
            "ollama agent 轮次 %d 正文=%d 工具=%d done=%s",
            i + 1, len(spoken), len(calls), sink.get("done_reason") or "-",
        )
        if sink.get("done_reason") == "length":
            if progress is not None:
                progress.save()
                thread.append({"role": "assistant", "content": spoken or ""})
                more = "上一轮回复被截断。\n" + progress.digest() + "\n请从进度接着完成还没做完的部分，不要从头开始。"
                thread.append({"role": "user", "content": more})
                _compact_old_tools(thread, digest=progress.digest(), aggressive=True, progress=progress)
                if on_status:
                    on_status("上一轮回复被截断，已记下进度，继续…")
                continue
            return
        if not calls:
            if stop_review is not None or repair_save_target:
                critique = ("还没有写入 " + repair_save_target + "。必须调用 save_article 并传入非空 content 正文。") if repair_save_target else str(stop_review(cleaned_spoken) or "").strip()
                if critique:
                    if reviews_left <= 0:
                        if progress is not None:
                            progress.save()
                        yield "\n未完成：模型连续只回复文字而没有执行剩余任务。" + critique + _resume_note(progress, saved)
                        return
                    reviews_left -= 1
                    log.info("ollama agent 自检未通过，继续")
                    thread.append({"role": "assistant", "content": spoken or ""})
                    thread.append({
                        "role": "user",
                        "content": "自检未通过。\n" + critique + "\n请继续调用工具完成任务，不要结束。",
                    })
                    if on_status:
                        on_status("自检未通过，继续执行…")
                    continue
            if not spoken:
                thought = "".join(sink.get("thinking") or []).strip()
                if thought:
                    yielded = True
                    yield thought
            elif on_status:
                n_saved = len(saved)
                if progress is not None and progress.success_met():
                    on_status(_stage_status(progress, "已完成", "成功标准已满足"))
                else:
                    on_status(f"已完成，正文 {len(cleaned_spoken)} 字，保存 {n_saved} 次。")
            return

        reviews_left = 3
        results = []
        # 若本轮回复较短，但上一轮模型刚写出了长篇正文且这轮立即调 save_article，允许回退使用上一轮正文
        effective_spoken = cleaned_spoken if len(cleaned_spoken.strip()) >= 40 else (last_substantive_spoken or cleaned_spoken)
        for call in calls:
            if _stopped(should_stop):
                if progress is not None:
                    progress.save()
                return
            fn = call.get("function") or {}
            name = str(fn.get("name") or "").strip()
            args = fn.get("arguments") if isinstance(fn.get("arguments"), dict) else {}
            args = args or {}
            bits = []
            for key in ("filename", "name", "path", "url", "query"):
                val = str(args.get(key) or "").strip()
                if val:
                    bits.append(f"{key}={val[:80]}")
            if args.get("index") not in (None, ""):
                bits.append("index=" + str(args.get("index")))
            log.info("ollama agent 调用 %s %s", name, " ".join(bits))
            if on_status:
                on_status(_stage_status(progress, "执行", _exec_status(name, args)))
            unnoted = [f for f in progress.unnoted_reads() if f in pending_note_files] if progress is not None else []
            if name in ("read_article", "read_unread_batch") and unnoted and "save_notes" in _tool_names(tools):
                result = progress.notes_gate_text(unnoted)
            else:
                result = exec_tool(name, args, effective_spoken)
                if progress is not None and name in ("read_article", "read_unread_batch") and not _tool_failed(result, name):
                    pending_note_files.update(_files_of(result) or getattr(progress, "last_served", []) or [])
            if isinstance(result, str) and result.startswith(CATALOG_DONE_MARK):
                body = result[len(CATALOG_DONE_MARK):].lstrip("\n")
                first = body.split("\n", 1)[0].strip()
                log.info("ollama agent 目录执行结束 %s", first)
                if on_status and first:
                    on_status(first)
                yield "\n" + body
                return
            results.append(result)
            if name == "save_article" and result.startswith("已保存："):
                saved.append(result.split("\n", 1)[0])
                last_substantive_spoken = ""
                repair_save_target = ""
            where = _stuck_where(name, args)
            gated = (result or "").lstrip().startswith("【请先写笔记】")
            if _tool_failed(result, name) and not gated:
                reason = (result or "").split("\n", 1)[0].strip()
                key = (name, where)
                empty_self = (result or "").lstrip().startswith("没有保存：没有传入正文")
                if empty_self:
                    repair_save_target = str(args.get("filename") or args.get("name") or args.get("path") or "").strip()
                    result += "\n【保存纠错】下一轮只开放 save_article。filename 必须为 " + repair_save_target + "，content 必须填写这个文件的实际正文（每段不超过1000字）。不要再次只传文件名。"
                    results[-1] = result
                log.warning("ollama tool failed name=%s target=%s reason=%s", name, _target_of(name, args), reason)
                if key == last_fail:
                    if empty_self and not content_hint:
                        content_hint = where
                    else:
                        if on_status:
                            on_status(f"未完成：卡在{where}。原因：{reason}")
                        note = f"\n未完成：卡在{where}。同一调用已连续失败两次，先停。"
                        if reason:
                            note += "\n原因：" + reason
                        if name in ("read_article", "read_unread_batch"):
                            note += "\n请改用 list_articles 清单里的完整 path（引号内原样复制，不要拆开空格或逗号、顿号）或 index=编号。"
                        if saved:
                            note += "\n已保存：" + "；".join(saved)
                        yield note
                        return
                last_fail = key
            else:
                last_fail = None
                content_hint = None

        stall_note = ""
        if progress is not None:
            try:
                mark = progress.progress_mark()
            except Exception:
                mark = None
            if mark is not None and mark == last_mark:
                idle_rounds += 1
            else:
                idle_rounds = 0
            last_mark = mark
            if idle_rounds >= STALL_STOP_ROUNDS:
                progress.save()
                log.info("ollama agent 连续 %d 轮没有新进展，停下", idle_rounds)
                if on_status:
                    on_status("没有新进展，已停下；进度已记下")
                yield (
                    "\n最近几轮一直在重复同样的操作，没有新进展，程序先停在这里。"
                    "\n进度已记下。发送「继续」会从记录接着做。"
                    + ("\n" + progress.status_line() if progress.status_line() else "")
                    + ("\n已保存：" + "；".join(saved) if saved else "")
                )
                return
            if idle_rounds >= STALL_STEER_ROUNDS:
                log.info("ollama agent 连续 %d 轮没有新进展，提示下一步", idle_rounds)
                stall_note = (
                    "\n【程序提示】最近几轮没有新进展（重复同样的调用，结果不变）。"
                    + progress.next_action_text()
                )
            try:
                done_now = len(progress.completed_files())
                if saved_count_changed(saved, progress):
                    done_at_output = done_now
                    read_steer_at = 0
                missing = progress.missing_outputs()
                since = done_now - done_at_output
                if (missing and not stall_note and since >= READ_ONLY_STEER_FILES
                        and since - read_steer_at >= READ_ONLY_STEER_FILES // 2):
                    read_steer_at = since
                    log.info("ollama agent 自上次保存产出以来又读完 %d 个文件，提示先写产出", since)
                    done_list = progress.completed_files()
                    stall_note = (
                        "\n【程序提示】自上次保存产出以来，你已经又读完 %d 个文件（共读完 %d 个：%s）。"
                        "读取本身不是目标：请先用 save_article 保存还欠的产出（%s），"
                        "以后需要时可以再读、再用同名文件覆盖更新；不要只是继续读下一个。"
                        % (since, len(done_list), "、".join(done_list[:8]) + ("…" if len(done_list) > 8 else ""),
                           "、".join(missing[:5]))
                    )
            except Exception:
                pass

        thread.append({
            "role": "assistant",
            "content": spoken or "",
            "tool_calls": calls,
        })

        # 构建精简尾部提示（附在最后一个 tool 消息末尾，不伪造 role="user" 消息）
        tail_notes = []
        if content_hint:
            tail_notes.append(
                f"保存{content_hint}失败，是因为没有正文。"
                "请把该文件的完整正文放进 save_article 的 content 参数再保存，"
                "或先在回复里写出该文件全文。不要只传文件名。"
            )
        if progress is not None:
            try:
                progress.set_stage("观察")
                progress.set_stage("反思")
            except Exception:
                pass
            status_brief = progress.status_line()
            missing = progress.missing_outputs()
            if "save_notes" in _tool_names(tools):
                unnoted = progress.unnoted_reads()
                if unnoted:
                    tail_notes.append("请先调用 save_notes，files 精确填写本次阅读的文件，记录当前片段的事实、分析和续读位置；每条不超过 1000 字。原文不会在有笔记之前被压缩。")
            if missing:
                status_brief += " | 待保存产出：" + "、".join(missing[:5])
            tail_notes.append(f"【进度简报】{status_brief}")
            if progress.success_met():
                tail_notes.append(progress.success_stop_text())
                wrap_offered = True
                if on_status:
                    on_status(_stage_status(progress, "已完成", "成功标准已满足"))
        if stall_note:
            tail_notes.append(stall_note.strip())

        for idx, (c, r) in enumerate(zip(calls, results)):
            nm = str(((c.get("function") or {}).get("name")) or "")
            ag = ((c.get("function") or {}).get("arguments")) if isinstance((c.get("function") or {}).get("arguments"), dict) else {}
            obs = format_observation(nm, ag or {}, r)
            if idx == len(calls) - 1 and tail_notes:
                obs = obs.rstrip() + "\n\n" + "\n".join(tail_notes)
            thread.append({
                "role": "tool",
                "name": nm,
                "content": obs,
            })
            if on_status and nm == "save_notes" and str(r).startswith("已记下笔记"):
                on_status("已记录")
            elif on_status and nm == "save_article" and str(r).startswith("已保存："):
                on_status("已记录")

        if yielded:
            yield "\n"
        think = False
        if progress is not None and progress.success_met() and wrap_offered:
            continue

    if progress is not None:
        progress.save()
    if on_status:
        on_status("进度已记下。请再发一条消息，程序会从记录接着做。")
    yield _resume_note(progress, saved)
