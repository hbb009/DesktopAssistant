# utils/ollama_chat.py
# ---------------------------------------------------------------------------
# 本地 Ollama 对话：模型列表 + /api/chat 流式输出（纯函数，不碰 Qt）
# 针对本地 30B+ GGUF 模型（如 qwen3.6-jailbreak:q6）升级：
#   1. 自动拦截并分流混在 content 中的 <think>...</think> 标签，避免思考文本刷屏或被误存进文件；
#   2. 限制单轮思考上限（2400 字），防止模型长篇空想挤占 30720 上下文；
#   3. 兼容没有配置原生 Tools 模板的自定义 GGUF 模型（在系统提示中注入精简工具定义，并支持多种工具调用格式解析）。
# ---------------------------------------------------------------------------

from __future__ import annotations

import base64
import codecs
import json
import re
import threading
from datetime import date
from html import unescape
from typing import Callable, Iterator, List, Optional
from urllib.parse import parse_qs, urlparse

import requests

from utils.logger import get_logger
from utils import ollama_auto as _A
from utils.ollama_auto import OUTPUT_RESERVE
from utils.ollama_dirs import TOOL_NAMES as _DIR_TOOL_NAMES
from utils.ollama_dirs import DirBudget, run_dir_tool, status_text as _dir_status
from utils.ollama_intent import classify as classify_intent
from utils.ollama_intent import folder_system as intent_folder_system

log = get_logger("deskassist.ollama")

DEFAULT_BASE = "http://127.0.0.1:11434"
# 收紧单轮思考上限，防止推理模型长篇空想撑爆 30720 上下文
_THINK_CAP = 2400

# 上下文长度（token）。默认对齐 30720 稳态窗口。
DEFAULT_NUM_CTX = 30720
MIN_NUM_CTX = 2048
MAX_NUM_CTX = 131072
_CTX = threading.local()
_CJK_RE = re.compile(r"[\u3000-\u303f\u3400-\u9fff\uf900-\ufaff\uff00-\uffef]")

DEFAULT_THINK_RATIO = 0.08     # 思考最多占上下文的比例（按字折算）
DEFAULT_MAX_CONTINUE = 8       # 回答被长度截断后自动续写的次数
_THINK_HANDOVER = (
    "你已经思考得足够多，现在直接输出正文或调用工具，不要再重复读取已读文件；材料已在上文。"
    "保存时调用 save_article，必须同时传 filename 与 content 正文。"
)


def _think_cap() -> int:
    """思考字数上限：控制在 1500–3600 字以内，避免长考挤占正文空间。"""
    n = _ctx_num() or DEFAULT_NUM_CTX
    try:
        r = float(getattr(_CTX, "think_ratio", 0) or DEFAULT_THINK_RATIO)
    except Exception:
        r = DEFAULT_THINK_RATIO
    return max(1500, min(4000, int(n * r * 1.2)))


def clamp_num_ctx(value) -> int:
    try:
        n = int(value)
    except Exception:
        return DEFAULT_NUM_CTX
    return max(MIN_NUM_CTX, min(MAX_NUM_CTX, n))


def estimate_tokens(text) -> int:
    """保守估计 token：中日韩按一字一个，其余按三字符一个；包含工具参数。"""
    s = str(text or "")
    cjk = len(_CJK_RE.findall(s))
    return int(cjk + (len(s) - cjk) / 3) + 1


def fit_history(messages: list, num_ctx: int, reserve_tokens: int = 0, ratio: float = 0.8):
    """按上下文长度裁掉最早的几轮。system 和最后一条用户消息永远保留。"""
    msgs = [dict(m) for m in (messages or []) if isinstance(m, dict)]
    if not msgs:
        return [], 0, 0, False
    num_ctx = clamp_num_ctx(num_ctx)
    reserve = max(0, int(reserve_tokens or 0))
    last_u = len(msgs) - 1
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].get("role") == "user":
            last_u = i
            break

    def cost(m):
        text = str(m.get("content") or "")
        extra = str(m.get("attach_text") or "")
        if extra:
            text = text + "\n" + extra
        return estimate_tokens(text) + 800 * len(m.get("images") or []) + 4

    keep = {i for i, m in enumerate(msgs) if m.get("role") == "system" or i >= last_u}
    used = sum(cost(msgs[i]) for i in keep)
    last_tokens = cost(msgs[last_u])
    over = last_tokens + reserve > num_ctx
    budget = int(num_ctx * ratio) - reserve
    for i in range(last_u - 1, -1, -1):
        if i in keep:
            continue
        c = cost(msgs[i])
        if used + c > budget:
            break
        keep.add(i)
        used += c
    kept = [m for i, m in enumerate(msgs) if i in keep]
    return kept, len(msgs) - len(kept), last_tokens, over


def _ctx_num() -> int:
    return int(getattr(_CTX, "num_ctx", 0) or 0)


def _ctx_char_cap() -> int:
    """限制阅读片段，给分析笔记、工具协议和文件输出预留空间。"""
    n = _ctx_num() or DEFAULT_NUM_CTX
    return max(512, min(4000, int(n * 0.15)))


def _cap_text(text: str, cap: int) -> str:
    text = text or ""
    if cap > 0 and len(text) > cap:
        return text[:cap] + f"\n…（后面已截断：受上下文长度限制，只给了前 {cap} 字）"
    return text


def _notice(text: str) -> None:
    fn = getattr(_CTX, "on_notice", None)
    if fn is not None and text:
        try:
            fn(text)
        except Exception:
            log.exception("ollama notice 回调失败")


def _mark_truncated() -> None:
    if getattr(_CTX, "truncated", False):
        return
    _CTX.truncated = True
    n = _ctx_num() or DEFAULT_NUM_CTX
    saved = "，所以没有保存文件" if getattr(_CTX, "dirs", False) else ""
    _notice(
        f"回答太长没能写完{saved}：程序已自动关闭思考、使用上下文 {n}，并自动续写，仍然被截断。"
        "已写出的部分在上面；发送「继续」可以接着写，内容很长时可以让我分段处理。"
    )
    fn = getattr(_CTX, "on_status", None)
    if fn is not None:
        try:
            fn("未完成（被截断）")
        except Exception:
            pass


class OllamaError(Exception):
    """带中文说明的 Ollama 错误。"""


def normalize_base(base: str) -> str:
    s = (base or "").strip().rstrip("/")
    if not s:
        return DEFAULT_BASE
    if not s.startswith(("http://", "https://")):
        s = "http://" + s
    return s


def _http_detail(resp) -> str:
    if resp is None:
        return ""
    try:
        body = resp.json() or {}
    except Exception:
        return (resp.text or "")[:200]
    err = body.get("error", "") if isinstance(body, dict) else ""
    if isinstance(err, dict):
        return str(err.get("message") or err)[:200]
    if err:
        return str(err)[:200]
    return ""


def _friendly(exc: Exception, base: str, who: str = "Ollama") -> OllamaError:
    if isinstance(exc, OllamaError):
        return exc
    if isinstance(exc, requests.ConnectionError):
        if who == "Ollama":
            return OllamaError(f"连接不上 Ollama（{base}）。请确认 Ollama 已启动，地址和端口填写正确。")
        return OllamaError(f"连接不上 API（{base}）。请确认地址和密钥填写正确。")
    if isinstance(exc, requests.Timeout):
        if who == "Ollama":
            return OllamaError(f"Ollama 响应超时（{base}）。模型可能正在加载，请稍后再试。")
        return OllamaError(f"API 响应超时（{base}）。请稍后再试。")
    if isinstance(exc, requests.HTTPError):
        resp = exc.response
        detail = _http_detail(resp)
        code = resp.status_code if resp is not None else "?"
        if code == 404:
            return OllamaError(f"模型不存在或接口不可用（HTTP 404）：{detail or '请刷新模型列表'}")
        label = "Ollama" if who == "Ollama" else "API"
        return OllamaError(f"{label} 返回错误（HTTP {code}）：{detail or '未知错误'}")
    if who == "Ollama":
        return OllamaError(f"请求 Ollama 出错：{exc}")
    return OllamaError(f"请求 API 出错：{exc}")


def _fmt_bytes(n) -> str:
    try:
        n = float(n or 0)
    except Exception:
        return ""
    if n <= 0:
        return ""
    units = ("B", "KB", "MB", "GB", "TB")
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024.0
        i += 1
    if i >= 2:
        return f"{n:.1f} {units[i]}"
    return f"{int(n)} {units[i]}"


def _fmt_ctx(n) -> str:
    try:
        n = int(n or 0)
    except Exception:
        return ""
    if n <= 0:
        return ""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        v = n / 1000
        return f"{int(v)}K" if abs(v - int(v)) < 0.05 else f"{v:.1f}K"
    return str(n)


def list_model_details(base: str = DEFAULT_BASE, timeout: float = 8.0) -> list:
    """GET /api/tags → 名称、参数、量化、体积、上下文。失败抛 OllamaError。"""
    base = normalize_base(base)
    try:
        r = requests.get(f"{base}/api/tags", timeout=timeout)
        r.raise_for_status()
        models = (r.json() or {}).get("models", []) or []
    except Exception as e:
        raise _friendly(e, base)
    rows = []
    for m in models:
        if not isinstance(m, dict):
            continue
        name = str(m.get("name") or m.get("model") or "").strip()
        if not name:
            continue
        d = m.get("details") or {}
        if not isinstance(d, dict):
            d = {}
        rows.append({
            "name": name,
            "params": str(d.get("parameter_size") or ""),
            "quant": str(d.get("quantization_level") or ""),
            "size": _fmt_bytes(m.get("size")),
            "ctx": _fmt_ctx(d.get("context_length")),
        })
    rows.sort(key=lambda row: row["name"])
    return rows


def list_models(base: str = DEFAULT_BASE, timeout: float = 3.0) -> List[str]:
    """GET /api/tags → 模型名列表。失败抛 OllamaError。"""
    return [row["name"] for row in list_model_details(base, timeout=timeout)]


_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_WEB_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "搜索互联网，返回网页标题、链接和摘要。需要最新或不确定的事实时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "打开一个 http 或 https 网页并读取正文。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要打开的网址"},
                },
                "required": ["url"],
            },
        },
    },
]

_WEB_SYSTEM = (
    "联网已开启。需要最新或不确定的事实时，先调用 web_search 搜索，"
    "要看某一页再调用 web_fetch。拿到结果后用简体中文直接回答，并写上来源标题或网址。"
    "用户说「今天」时，按下面给出的本地日期理解，不要改成别的日期。"
)

_DIR_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_articles",
            "description": (
                "列出工作目录里的文件和子文件夹。默认递归列出子目录中的 .md/.txt，每项带 #编号 和 path=完整相对路径。"
                "要只看某个子文件夹，传入 path 或 index。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要列出的子文件夹相对路径"},
                    "index": {"type": "integer", "description": "清单中的文件夹编号"},
                    "recursive": {"type": "boolean", "description": "是否递归列出子目录，默认 true"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rename_article",
            "description": (
                "只改工作目录里的文件名，不读取、不改写文件内容。"
                "filename 或 index 指定原文件；new_filename 可省略（省略时程序只保留原名中的汉字和数字，并保留扩展名）。"
                "也可传入 renames 列表，或 mode=\"han_digits\" 批量处理清单中的文件。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "原文件的完整相对路径"},
                    "index": {"type": "integer", "description": "list_articles 清单中的编号，从 1 开始"},
                    "new_filename": {"type": "string", "description": "新文件名（可省略）"},
                    "mode": {"type": "string", "description": "han_digits 表示按「只留汉字和数字」批量处理"},
                    "renames": {
                        "type": "array",
                        "description": "批量重命名列表，每项含 filename/index 及可选 new_filename",
                        "items": {"type": "object"},
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_article",
            "description": (
                "阅读工作目录里的一篇 .md 或 .txt（可含子目录）。"
                "优先传 index=清单编号（如 1），或把 path= 引号内的完整相对路径原样复制到 filename（不要拆开空格或标点）。"
                "文件很长时返回里会提示 start=数字，请用同一 filename/index 带上该 start 继续读完。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "完整相对路径，不要拆开空格和标点"},
                    "index": {"type": "integer", "description": "清单中的编号，从 1 开始（与 filename 二选一）"},
                    "start": {"type": "integer", "description": "从第几个字继续读，默认 0"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_unread_batch",
            "description": "读取下一个尚未处理完的文件（一次只读一个）。一般请直接用 read_article 指定 index 或 filename。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_notes",
            "description": "分页读取已保存的分析笔记，续做任务或原始上下文被压缩时使用。",
            "parameters": {"type": "object", "properties": {"start": {"type": "integer", "description": "起始字符位置，默认0"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_notes",
            "description": "记录当前阅读片段的分析笔记，阅读下一片段前调用。content 写关键发现与下一步计划。",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "阶段性发现与下一步计划"},
                    "files": {"type": "string", "description": "覆盖的文件相对路径（可选）"},
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_article",
            "description": (
                "把文章保存到用户指定的工作目录。filename 写相对路径（可含子目录，程序会自动创建）。"
                "必须传入 content 实际正文，不能只传 filename；长文分段，每段不超过1000字。"
                "仅保存已抓取网页时允许 content 为空字符串，须同时传入 url 以使用网页缓存。"
                "mode=\"overwrite\"（默认）覆盖；mode=\"append\" 追加。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "完整相对路径，例如 第一章.md 或 子目录/说明.md"},
                    "content": {"type": "string", "description": "正文，长文每次不超过1000字，分段追加"},
                    "url": {"type": "string", "description": "若保存刚打开的网页，填该页网址"},
                    "complete": {"type": "boolean", "description": "文件是否全部写完，分段写入的中间段必须为 false，最后一段为 true，默认 true"},
                    "mode": {
                        "type": "string",
                        "enum": ["overwrite", "append"],
                        "description": "overwrite 覆盖（默认），append 追加到末尾",
                    },
                },
                "required": ["filename", "content"],
            },
        },
    },
]


def _dir_system(folder: str) -> str:
    where = (folder or "").strip() or "（还没有选择）"
    return (
        f"工作目录：{where}。所有文件读写都限定在此目录内，filename 写相对路径（不要写盘符）。\n"
        "查看目录用 list_articles；读取文章用 read_article（优先传 index=编号 或完整 filename）；"
        "保存文章用 save_article（必须同时传 filename 和 content 正文；长文可用 mode=\"append\" 分段追加）；"
        "若只改文件名不改内容，只用 list_articles 和 rename_article。\n"
        "请以工具实际返回为准，未调用 save_article 并收到「已保存：」前不要声称已保存。"
    )


def _format_tools_prompt_fallback(tools: list) -> str:
    """针对自定义 GGUF 模型（如 qwen3.6-jailbreak:q6，其 Modelfile 可能未包含 {{ .Tools }} 渲染）：
    在 system 提示词末尾附上精简工具签名说明，确保即使底层模板丢弃 tools 字段，模型也能准确发起 <tool_call>。"""
    if not tools:
        return ""
    lines = [
        "【可用工具与调用格式】",
        "如需调用工具，请优先使用原生 tool_calls；若直接输出文本工具调用，请严格按以下格式输出：",
        "<tool_call>",
        "{\"name\": \"工具名\", \"arguments\": {\"参数名\": \"参数值\"}}",
        "</tool_call>",
        "当前可用工具列表：",
    ]
    for t in tools:
        fn = (t or {}).get("function") or {}
        name = fn.get("name")
        if not name:
            continue
        props = ((fn.get("parameters") or {}).get("properties")) or {}
        req = set(((fn.get("parameters") or {}).get("required")) or [])
        arg_bits = []
        for k, v in props.items():
            mark = "*" if k in req else ""
            arg_bits.append(f"{k}{mark}:{v.get('type', 'string')}")
        lines.append(f"- {name}({', '.join(arg_bits)}): {(fn.get('description') or '')[:90]}")
    return "\n".join(lines)


_TOOL_OPEN = "<tool_call>"
_TOOL_CLOSE = "</tool_call>"
_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"
_FN_RE = re.compile(r"<function=([^\s>/]+)>(.*?)</function>", re.S | re.I)
_PARAM_RE = re.compile(
    r"<parameter=(?:name=)?[\"']?([^\s>\"']+)[\"']?>(.*?)</parameter>",
    re.S | re.I,
)


def _real_url(href: str) -> str:
    """必应结果常包在 /ck/a 跳转里，u=a1+base64 才是真正的网址。"""
    href = unescape(href or "").replace("&amp;", "&").strip()
    if href.startswith("//"):
        href = "https:" + href
    elif href.startswith("/"):
        href = "https://www.bing.com" + href
    try:
        packed = (parse_qs(urlparse(href).query).get("u") or [""])[0]
    except Exception:
        return href
    if not packed.startswith("a1"):
        return href
    raw = packed[2:]
    try:
        out = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", "replace").strip()
    except Exception:
        return href
    return out if out.startswith(("http://", "https://")) else href


def _html_text(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript)\b[^>]*>.*?</\1>", " ", raw or "")
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _calendar_hint(query: str) -> str:
    """农历问题：必应常返回整年放假表，另外打开当天万年历页摘一句。"""
    if not any(k in (query or "") for k in ("农历", "阴历", "黄历", "老黄历")):
        return ""
    m = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", query or "")
    if m:
        try:
            day = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            day = date.today()
    else:
        day = date.today()
    week = "一二三四五六日"[day.weekday()]
    head = f"公历 {day.year}年{day.month}月{day.day}日 星期{week}。"
    url = f"https://www.rili.com.cn/wannianli/{day.year}/{day.month:02d}{day.day:02d}.html"
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=8)
        r.raise_for_status()
        text = _html_text(r.text)
    except Exception as e:
        return head + f"（万年历页面没有打开：{e}）"
    hit = re.search(r"农历[：:][^，。\n]{2,24}", text)
    if not hit:
        i = text.find("农历")
        snippet = text[i:i + 40] if i >= 0 else ""
    else:
        snippet = hit.group(0)
    return (head + " " + snippet).strip()


def web_search(query: str, limit: int = 5) -> str:
    """用必应取几条网页结果。失败时返回说明，不往外抛。"""
    query = re.sub(r"\s+", " ", (query or "").strip())[:200]
    if not query:
        return "搜索词为空。"
    hint = _calendar_hint(query)
    try:
        r = requests.get(
            "https://www.bing.com/search",
            params={"q": query, "count": str(limit)},
            headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"},
            timeout=12,
        )
        r.raise_for_status()
    except Exception as e:
        note = f"搜索失败：{e}"
        return (hint + "\n\n" + note) if hint else note
    rows = []
    pat = re.compile(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
    snip = re.compile(r"<p[^>]*>(.*?)</p>", re.S)
    for part in r.text.split('<li class="b_algo"')[1:]:
        m = pat.search(part)
        if not m:
            continue
        url = _real_url(m.group(1))
        if not url.startswith(("http://", "https://")):
            continue
        title = _html_text(m.group(2))[:160]
        snippet = _html_text((snip.search(part).group(1) if snip.search(part) else ""))[:300]
        rows.append(f"{len(rows) + 1}. {title}\n{url}\n{snippet}".strip())
        if len(rows) >= limit:
            break
    body = "\n\n".join(rows) if rows else "没有搜到网页结果。"
    if hint:
        return hint + "\n\n" + body
    return body


def web_fetch(url: str, limit: int = 20000) -> str:
    """打开网页并抽出正文和链接。只接受 http/https。"""
    url = (url or "").strip()
    if url.startswith("www."):
        url = "https://" + url
    if not url.startswith(("http://", "https://")):
        return "只能打开 http 或 https 地址。"
    try:
        page = _A.fetch_page(url)
        title = (page.get("title") or "").strip()
        text = (page.get("text") or "").strip()
        links = page.get("links") or []
        from utils.ollama_agent import remember_fetch
        remember_fetch(page.get("url") or url, title, text)
        full_n = len(text)
        keep = 600
        if full_n > keep:
            shown = (
                text[:keep].rstrip()
                + f"\n…（正文共 {full_n} 字，已缓存。保存时调用 save_article，传 filename、content=空字符串，"
                "需要时加上这个 url。不要把正文抄进回复。）"
            )
        else:
            shown = text or "页面没有读到正文。"
        head = f"【已打开网页】标题：《{title or '无标题'}》 字数：{full_n}\n网址：{page.get('url') or url}\n\n"
        if links:
            lines = [f"- {name}\n  {href}" for name, href in links[:40]]
            shown = (shown + "\n\n页面上的链接：\n" + "\n".join(lines)).strip()
        if limit and len(shown) > limit:
            shown = shown[:limit] + "\n…（后面已截断）"
        return (head + shown).strip()
    except Exception as e:
        return f"打开网页失败：{e}"


def _normalize_call(call: dict) -> dict:
    fn = call.get("function") if isinstance(call, dict) else {}
    if not isinstance(fn, dict):
        fn = {}
    name = str(fn.get("name") or "").strip()
    args = fn.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except Exception:
            args = {"query": args}
    if not isinstance(args, dict):
        args = {}
    return {"type": "function", "function": {"name": name, "arguments": args}}


def _merge_tool_calls(acc: list, incoming) -> list:
    if isinstance(incoming, dict):
        incoming = [incoming]
    if not isinstance(incoming, list):
        return acc
    for i, call in enumerate(incoming):
        if not isinstance(call, dict):
            continue
        if i >= len(acc):
            acc.append(call)
            continue
        old_fn = (acc[i].get("function") or {}) if isinstance(acc[i], dict) else {}
        new_fn = call.get("function") or {}
        name = new_fn.get("name") or old_fn.get("name") or ""
        old_args = old_fn.get("arguments")
        new_args = new_fn.get("arguments")
        if isinstance(new_args, dict) and new_args:
            args = new_args
        elif isinstance(new_args, str) and new_args:
            args = (old_args + new_args) if isinstance(old_args, str) else new_args
        else:
            args = old_args
        acc[i] = {"type": "function", "function": {"name": name, "arguments": args}}
    return acc


def _run_tool(
    name: str,
    args: dict,
    on_status: Optional[Callable[[str], None]],
    should_stop: Optional[Callable[[], bool]] = None,
    budget: Optional[DirBudget] = None,
    dirs: bool = False,
    article_dir: str = "",
    reply_body: str = "",
    notices: Optional[list] = None,
    progress=None,
) -> str:
    cap = _ctx_char_cap()
    if name in _DIR_TOOL_NAMES:
        if not dirs:
            return "文章目录没有开启。"
        if on_status:
            on_status(_dir_status(name, args))
        return run_dir_tool(
            name, args, article_dir=article_dir,
            should_stop=should_stop, budget=budget,
            reply_body=reply_body, read_chars=cap, notices=notices,
            progress=progress,
        )
    if name == "web_search":
        query = str(args.get("query") or args.get("q") or "").strip()
        if on_status:
            on_status(f"正在搜索：{query or '…'}")
        return _cap_text(web_search(query), min(15000, cap))
    if name == "web_fetch":
        url = str(args.get("url") or "").strip()
        if on_status:
            on_status("正在打开网页…")
        return _cap_text(web_fetch(url, limit=max(20000, cap)), cap)
    return f"没有这个工具：{name}"


def _fold_attachments(messages: list) -> list:
    """把界面附上的文本和图片并进发给模型的副本。不改调用方手里的原消息。"""
    out = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        item = dict(m)
        extra = str(item.pop("attach_text", "") or "").strip()
        item.pop("files", None)
        if extra:
            base = str(item.get("content") or "").rstrip()
            item["content"] = f"{base}\n\n{extra}" if base else extra
        images = [str(x) for x in (item.get("images") or []) if str(x).strip()]
        if images:
            item["images"] = images
        else:
            item.pop("images", None)
        out.append(item)
    return out


def _last_user_text(messages: list) -> str:
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            return str(m.get("content") or "").strip()
    return ""


def _last_assistant_text(messages: list) -> str:
    for m in reversed(messages or []):
        if isinstance(m, dict) and m.get("role") == "assistant":
            return str(m.get("content") or "").strip()
    return ""


_NO_TOOLS_RE = re.compile(
    r"does not support tools|doesn't support tools|do not support tools|"
    r"not support(?:ed)?\s+(?:for\s+)?tools?|tools?\s+(?:are|is)\s+not\s+supported|"
    r"no tool support|tool(?:s| use| calling)? (?:is |are )?unsupported",
    re.I,
)


def _no_tool_support(exc: Exception) -> bool:
    text = str(exc or "")
    try:
        from utils.ollama_agent import bad_tool_args_error
        if bad_tool_args_error(text):
            return False
    except Exception:
        pass
    return bool(_NO_TOOLS_RE.search(text))


def _tag_prefix_suffix(text: str, tag: str) -> str:
    """文本末尾若可能是 tag 的开头，返回这段，避免标签被拆开展示。"""
    low = text.lower()
    token = tag.lower()
    max_n = min(len(low), len(token) - 1)
    for n in range(max_n, 0, -1):
        if token.startswith(low[-n:]):
            return text[-n:]
    return ""


def _json_tool_calls(text: str) -> list:
    dec = json.JSONDecoder()
    out = []
    i = 0
    while True:
        j = text.find("{", i)
        if j < 0:
            break
        try:
            obj, end = dec.raw_decode(text[j:])
        except Exception:
            i = j + 1
            continue
        i = j + max(end, 1)
        if not isinstance(obj, dict):
            continue
        name = obj.get("name") or obj.get("tool") or obj.get("action")
        if not name:
            continue
        args = obj.get("arguments")
        if args is None:
            args = obj.get("parameters") or obj.get("args")
        if args is None:
            # 扁平 JSON：把除 name/tool/action 外的键作为参数
            args = {k: v for k, v in obj.items() if k not in ("name", "tool", "action")}
        elif isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except Exception:
                args = {"query": args}
        if not isinstance(args, dict):
            args = {}
        out.append({"type": "function", "function": {"name": str(name).strip(), "arguments": args}})
    return out


def _parse_text_tool_calls(text: str) -> list:
    """识别写在正文里的工具调用（支持 <tool_call>、<function=...> 等格式）。"""
    if not text:
        return []
    low = text.lower()
    blocks = []
    start = 0
    while True:
        i = low.find(_TOOL_OPEN, start)
        if i < 0:
            break
        j = low.find(_TOOL_CLOSE, i + len(_TOOL_OPEN))
        if j < 0:
            blocks.append(text[i + len(_TOOL_OPEN):])
            break
        blocks.append(text[i + len(_TOOL_OPEN):j])
        start = j + len(_TOOL_CLOSE)
    if not blocks and "<function=" in low:
        blocks = [text]
    calls = []
    seen = set()
    for block in blocks:
        found = False
        for m in _FN_RE.finditer(block):
            found = True
            args = {}
            for p in _PARAM_RE.finditer(m.group(2)):
                args[p.group(1).strip()] = p.group(2).strip()
            name = m.group(1).strip()
            key = (name, json.dumps(args, ensure_ascii=False, sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            calls.append({"type": "function", "function": {"name": name, "arguments": args}})
        if found:
            continue
        for call in _json_tool_calls(block):
            fn = call["function"]
            key = (fn["name"], json.dumps(fn["arguments"], ensure_ascii=False, sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            calls.append(call)
    return calls


class _ToolTextFilter:
    """流式正文里先扣下 <tool_call> 与混在 content 中的 <think>...</think>，不直接刷到聊天界面。"""

    def __init__(self):
        self.buf = ""
        self.calls: list = []
        self.thinking: list = []
        self._in_think = False

    def feed(self, piece: str) -> str:
        if not piece:
            return ""
        self.buf += piece
        out = []
        while self.buf:
            low = self.buf.lower()
            if self._in_think:
                end = low.find(_THINK_CLOSE)
                if end < 0:
                    keep = _tag_prefix_suffix(self.buf, _THINK_CLOSE)
                    emit_think = self.buf[:-len(keep)] if keep else self.buf
                    if emit_think:
                        self.thinking.append(emit_think)
                    self.buf = keep
                    break
                if end > 0:
                    self.thinking.append(self.buf[:end])
                self.buf = self.buf[end + len(_THINK_CLOSE):].lstrip("\r\n")
                self._in_think = False
                continue

            t_start = low.find(_THINK_OPEN)
            c_start = low.find(_TOOL_OPEN)
            starts = [x for x in (t_start, c_start) if x >= 0]
            if not starts:
                keep_t = _tag_prefix_suffix(self.buf, _THINK_OPEN)
                keep_c = _tag_prefix_suffix(self.buf, _TOOL_OPEN)
                keep = keep_t if len(keep_t) >= len(keep_c) else keep_c
                emit = self.buf[:-len(keep)] if keep else self.buf
                self.buf = keep
                if emit:
                    out.append(emit)
                break

            first = min(starts)
            if first > 0:
                out.append(self.buf[:first])
                self.buf = self.buf[first:]
                low = self.buf.lower()

            if low.startswith(_THINK_OPEN):
                self.buf = self.buf[len(_THINK_OPEN):]
                self._in_think = True
                continue

            # _TOOL_OPEN
            end = low.find(_TOOL_CLOSE, len(_TOOL_OPEN))
            if end < 0:
                break
            end += len(_TOOL_CLOSE)
            self.calls.extend(_parse_text_tool_calls(self.buf[:end]))
            self.buf = self.buf[end:]
        return "".join(out)

    def finish(self) -> str:
        rest = self.buf
        self.buf = ""
        if self._in_think:
            if rest:
                self.thinking.append(rest)
            self._in_think = False
            return ""
        if rest and ("<tool_call>" in rest.lower() or "<function=" in rest.lower()):
            self.calls.extend(_parse_text_tool_calls(rest))
            return ""
        return rest


def _stream_round(
    base: str,
    model: str,
    messages: list,
    tools: Optional[list],
    should_stop: Optional[Callable[[], bool]],
    on_response: Optional[Callable[[object], None]],
    connect_timeout: float,
    read_timeout: float,
    sink: dict,
    think: Optional[bool] = None,
    on_think: Optional[Callable[[int], None]] = None,
    on_status: Optional[Callable[[str], None]] = None,
) -> Iterator[str]:
    """一轮 /api/chat。正文逐段 yield；全文、思考和工具调用写入 sink。"""
    messages = _fold_attachments(messages)
    sink["content"] = []
    sink["thinking"] = []
    sink["calls"] = []
    sink["text_calls"] = []
    sink["done_reason"] = ""
    sink["think_runaway"] = False
    text_filter = _ToolTextFilter()
    think_n = 0
    think_mark = 0
    write_n = 0
    write_mark = 0
    payload = {"model": model, "messages": messages, "stream": True}
    if getattr(_CTX, "auto", False):
        _auto_ctx(messages, tools, think)
    num_ctx = _ctx_num()
    options = {}
    if num_ctx:
        options["num_ctx"] = num_ctx
    temp = getattr(_CTX, "temperature", None)
    if temp is not None:
        try:
            options["temperature"] = float(temp)
        except (TypeError, ValueError):
            pass
    if options:
        payload["options"] = options
    if tools:
        payload["tools"] = tools
    if think is not None:
        payload["think"] = bool(think)
    r = None
    try:
        r = requests.post(
            f"{base}/api/chat", json=payload, stream=True,
            timeout=(connect_timeout, read_timeout),
        )
        if r.status_code >= 400:
            # Consume error bodies before closing the streamed response.
            log.warning("ollama HTTP %s: %s", r.status_code, _http_detail(r))
        r.raise_for_status()
    except Exception as e:
        error = _friendly(e, base)
        try:
            if r is not None:
                r.close()
        except Exception:
            pass
        raise error
    if on_response is not None:
        try:
            on_response(r)
        except Exception:
            pass
    decoder = codecs.getincrementaldecoder("utf-8")()
    buf = ""
    try:
        for chunk in r.iter_content(chunk_size=None):
            if should_stop is not None and should_stop():
                return
            buf += decoder.decode(chunk or b"")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get("error"):
                    raise OllamaError(f"Ollama 错误：{obj.get('error')}")
                msg = obj.get("message") or {}
                _merge_tool_calls(sink["calls"], msg.get("tool_calls"))
                thinking = msg.get("thinking") or ""
                content = msg.get("content") or ""
                visible = ""
                if content:
                    prev_think_len = len(text_filter.thinking)
                    visible = text_filter.feed(content)
                    if len(text_filter.thinking) > prev_think_len:
                        thinking += "".join(text_filter.thinking[prev_think_len:])
                if thinking:
                    sink["thinking"].append(thinking)
                    think_n += len(thinking)
                    if on_think is not None and think_n - think_mark >= 400:
                        think_mark = think_n
                        try:
                            on_think(think_n)
                        except Exception:
                            pass
                    if think_n >= _think_cap() and not sink["content"] and not sink["calls"] and not text_filter.calls:
                        sink["think_runaway"] = True
                        if on_status is not None:
                            try:
                                on_status(f"思考超过 {_think_cap()} 字还没有正文，先停下来。")
                            except Exception:
                                pass
                        return
                if visible:
                    sink["content"].append(visible)
                    write_n += len(visible)
                    yield visible
                    if on_status is not None and write_n - write_mark >= 400:
                        write_mark = write_n
                        try:
                            on_status(f"正在写正文…已 {write_n} 字")
                        except Exception:
                            pass
                if obj.get("done"):
                    sink["done_reason"] = str(obj.get("done_reason") or "")
                    sink["prompt_eval_count"] = int(obj.get("prompt_eval_count") or 0)
                    sink["eval_count"] = int(obj.get("eval_count") or 0)
                    break
    except OllamaError:
        raise
    except Exception as e:
        if should_stop is not None and should_stop():
            return
        raise _friendly(e, base)
    finally:
        try:
            r.close()
        except Exception:
            pass
    prev_think_len = len(text_filter.thinking)
    tail = text_filter.finish()
    if len(text_filter.thinking) > prev_think_len:
        sink["thinking"].append("".join(text_filter.thinking[prev_think_len:]))
    sink["text_calls"] = list(text_filter.calls)
    if tail:
        sink["content"].append(tail)
        yield tail
    sink["num_ctx"] = num_ctx
    log.info(
        "ollama 轮次 num_ctx=%s done_reason=%s prompt_eval_count=%s eval_count=%s 正文=%d 思考=%d think=%s",
        num_ctx or "默认", sink.get("done_reason") or "-", sink.get("prompt_eval_count", 0),
        sink.get("eval_count", 0), write_n, think_n, think,
    )
    if sink.get("done_reason") == "length":
        _mark_truncated()


def _stopped(should_stop: Optional[Callable[[], bool]]) -> bool:
    return should_stop is not None and bool(should_stop())


def _round_with_continue(
    base, model, messages, tools, should_stop, on_response,
    connect_timeout, read_timeout, sink, think=None, on_think=None, on_status=None,
) -> Iterator[str]:
    """一轮回答；被 done_reason=length 截断时自动「继续」，最多 DEFAULT_MAX_CONTINUE 次。"""
    from utils.ollama_chunk import continue_prompt, trim_overlap
    on_status = on_status or getattr(_CTX, "on_status", None)
    prev_flag = getattr(_CTX, "truncated", False)
    _CTX.truncated = True
    k = 0
    retry = 0
    try:
        yield from _stream_round(
            base, model, messages, tools, should_stop, on_response,
            connect_timeout, read_timeout, sink, think=think, on_think=on_think, on_status=on_status,
        )
        written = "".join(sink.get("content") or [])
        while (
            (sink.get("done_reason") == "length" or sink.get("think_runaway"))
            and not written.strip() and not sink.get("calls") and not sink.get("text_calls")
            and retry < 2 and not _stopped(should_stop)
        ):
            retry += 1
            if getattr(_CTX, "auto", False) and not getattr(_CTX, "loaded", 0):
                _CTX.floor = _A.escalate(_ctx_num(), getattr(_CTX, "max_ctx", 0), getattr(_CTX, "loaded", 0))
            if on_status:
                on_status("思考较长，已切换为直接输出模式重试…")
            digest = _thinking_text(sink)[-600:]
            prompt_retry = _EMPTY_RETRY + (f"\n\n刚才的思考要点：\n{digest}" if digest else "")
            thread0 = list(messages) + [{"role": "user", "content": prompt_retry}]
            sink.clear()
            yield from _stream_round(
                base, model, thread0, tools, should_stop, on_response,
                connect_timeout, read_timeout, sink, think=False, on_status=on_status,
            )
            written = "".join(sink.get("content") or [])
            log.info("ollama 空正文重试第 %d 次 正文=%d done=%s", retry, len(written), sink.get("done_reason"))
        try:
            max_k = int(getattr(_CTX, "max_continue", DEFAULT_MAX_CONTINUE))
        except Exception:
            max_k = DEFAULT_MAX_CONTINUE
        while (
            sink.get("done_reason") == "length" and written.strip()
            and k < max_k and not _stopped(should_stop)
        ):
            k += 1
            if on_status:
                on_status(f"内容较长，已自动续写第 {k} 次…")
            if getattr(_CTX, "auto", False) and k >= 2 and not getattr(_CTX, "loaded", 0):
                _CTX.floor = _A.escalate(_ctx_num(), getattr(_CTX, "max_ctx", 0), getattr(_CTX, "loaded", 0))
            shown = written if len(written) <= 4000 else "……（前文已写，这里省略）" + written[-4000:]
            thread = list(messages) + [
                {"role": "assistant", "content": shown},
                {"role": "user", "content": continue_prompt(written)},
            ]
            part: dict = {}
            buf = "".join(_stream_round(
                base, model, thread, tools, should_stop, on_response,
                connect_timeout, read_timeout, part, think=False, on_status=on_status,
            ))
            if _stopped(should_stop):
                break
            new = trim_overlap(written, buf)
            log.info(
                "ollama 续写第 %d 次 新增=%d 接缝去重=%d done=%s",
                k, len(new), len(buf) - len(new), part.get("done_reason"),
            )
            if new:
                yield new
            written += new
            sink["content"] = [written]
            sink["calls"] = part.get("calls") or []
            sink["text_calls"] = part.get("text_calls") or []
            sink["done_reason"] = part.get("done_reason") or ""
            sink["think_runaway"] = False
    finally:
        _CTX.truncated = prev_flag
        sink["continued"] = k
    if sink.get("done_reason") == "length" and not _stopped(should_stop):
        sink["incomplete"] = True
        _mark_truncated()
    elif k and on_status:
        on_status(f"已续写 {k} 次，回答写完了。")


def _round_calls(sink: dict) -> list:
    native = [_normalize_call(c) for c in (sink.get("calls") or []) if isinstance(c, dict)]
    text_calls = [_normalize_call(c) for c in (sink.get("text_calls") or []) if isinstance(c, dict)]
    calls = text_calls or native
    return [c for c in calls if (c.get("function") or {}).get("name")]


def _one_copy(text: str) -> str:
    """思考里把同一篇稿子原样连写两三遍时，只留第一遍。"""
    s = (text or "").strip()
    if len(s) < 240:
        return s
    flat = re.sub(r"\s+", "", s)
    for k in (2, 3, 4, 5):
        if len(flat) % k:
            continue
        unit = flat[: len(flat) // k]
        if len(unit) < 80 or unit * k != flat:
            continue
        seen = 0
        for i, ch in enumerate(s):
            if not ch.isspace():
                if seen == len(unit):
                    return s[:i].strip()
                seen += 1
    return s


def _thinking_text(sink: dict) -> str:
    """模型把整段话写进思考、正文为空时，拿一份出来给用户看。"""
    raw = "".join(sink.get("thinking") or []).strip()
    if not raw:
        return ""
    filt = _ToolTextFilter()
    text = _one_copy((filt.feed(raw) + filt.finish()).strip())
    if len(text) > 30000:
        text = text[:30000].rstrip() + "\n…（后面被截断）"
    return text


def _log_round(kind: str, sink: dict, calls: list) -> None:
    log.info(
        "ollama %s 正文=%d 思考=%d 工具=%d num_ctx=%s done_reason=%s prompt_eval_count=%s eval_count=%s",
        kind,
        len("".join(sink.get("content") or [])),
        len("".join(sink.get("thinking") or [])),
        len(calls),
        sink.get("num_ctx", 0), sink.get("done_reason") or "-",
        sink.get("prompt_eval_count", 0), sink.get("eval_count", 0),
    )


_EMPTY_RETRY = (
    "上一次回答在写出正文之前就被截断或思考超限了。这次请不要输出 <think> 思考过程，直接输出正文或调用工具。"
    "需要保存时，把实际正文放进 save_article 的 content 参数。"
)


def _auto_ctx(messages: list, tools, think) -> int:
    """根据本轮全部消息、工具调用和输出预留选择上下文档位。"""
    loaded = getattr(_CTX, "loaded", 0)
    need = 0
    for m in messages or []:
        need += estimate_tokens(json.dumps(m, ensure_ascii=False)) + 8
        need += 800 * len(m.get("images") or [])
    if tools:
        need += estimate_tokens(json.dumps(tools, ensure_ascii=False))
    out = OUTPUT_RESERVE
    if getattr(_CTX, "dirs", False):
        last = next((str(m.get("content") or "") for m in reversed(messages or []) if m.get("role") == "user"), "")
        out = max(out, min(estimate_tokens(last), 16384))
    if think is not False:
        out += int(_THINK_CAP / 1.5)
    floor = max(int(getattr(_CTX, "last_ctx", 0) or 0), int(getattr(_CTX, "floor", 0) or 0))
    n = _A.choose_ctx(need + out, getattr(_CTX, "max_ctx", 0), loaded, floor)
    _CTX.num_ctx = _CTX.last_ctx = n
    return n


def _tools_named(names) -> list:
    allow = set(names or ())
    if not allow:
        return []
    return [t for t in list(_WEB_TOOLS) + list(_DIR_TOOLS) if t["function"]["name"] in allow]


def _stream_dirs(
    base: str,
    model: str,
    original: list,
    should_stop,
    on_response,
    connect_timeout: float,
    read_timeout: float,
    web: bool,
    on_status,
    article_dir: str,
    extra_system: str = "",
    on_think: Optional[Callable[[int], None]] = None,
    think: Optional[bool] = None,
    tool_names=None,
) -> Iterator[str]:
    """写文章并直接保存到用户指定的目录。"""
    if tool_names is None:
        tools = list(_DIR_TOOLS)
        notes = [_dir_system(article_dir)]
        if web:
            tools = list(_WEB_TOOLS) + tools
            notes.insert(0, _WEB_SYSTEM + f"今天的公历日期是 {date.today().isoformat()}。")
    else:
        tools = _tools_named(tool_names)
        notes = []
        if any(n in tool_names for n in ("web_search", "web_fetch")):
            notes.append(_WEB_SYSTEM + f"今天的公历日期是 {date.today().isoformat()}。")
        folder = intent_folder_system(article_dir, tuple(tool_names))
        if folder:
            notes.append(folder)
    if (extra_system or "").strip():
        notes.append(extra_system.strip())
    if tools:
        notes.append(_format_tools_prompt_fallback(tools))
    thread = list(original)
    if notes:
        thread = [{"role": "system", "content": "\n".join(notes)}] + thread
    budget = DirBudget()
    yielded = False
    need_gap = False
    bodies: list = []
    blocked: set = set()

    def spoken_of(sk):
        return "".join(sk.get("content") or []).strip()
    try:
        for attempt in range(4):
            if _stopped(should_stop):
                return
            sink: dict = {}
            for piece in _round_with_continue(
                base, model, thread, tools,
                should_stop, on_response, connect_timeout, read_timeout, sink,
                think=think, on_think=on_think, on_status=on_status,
            ):
                if piece and need_gap:
                    yield "\n"
                    need_gap = False
                if piece:
                    yielded = True
                    yield piece
            if _stopped(should_stop):
                return
            if sink.get("think_runaway") and not "".join(sink.get("content") or []):
                _log_round("目录·思考超限", sink, _round_calls(sink))
                digest = _thinking_text(sink)[-800:]
                has_material = any(m.get("role") == "tool" for m in thread)
                if has_material:
                    tools = [t for t in tools if t["function"]["name"] not in ("read_article", "read_unread_batch", "list_articles")]
                    blocked.update({"read_article", "read_unread_batch", "list_articles"})
                if on_status:
                    on_status("改为直接写正文" + ("（材料已在上文，这一轮不再读文件）。" if has_material else "。"))
                retry = list(thread) + [{
                    "role": "user",
                    "content": _THINK_HANDOVER + (f"\n\n你刚才思考的要点（结尾部分）：\n……{digest}" if digest else ""),
                }]
                sink = {}
                for piece in _round_with_continue(
                    base, model, retry, tools or None,
                    should_stop, on_response, connect_timeout, read_timeout, sink,
                    think=False, on_status=on_status,
                ):
                    if piece and need_gap:
                        yield "\n"
                        need_gap = False
                    if piece:
                        yielded = True
                        yield piece
                if _stopped(should_stop):
                    return
                if sink.get("think_runaway"):
                    if on_status:
                        on_status("模型仍在空转，这一轮已停下。")
                    return
            calls = _round_calls(sink)
            _log_round("目录", sink, calls)
            if blocked:
                skipped = [c for c in calls if c["function"]["name"] in blocked]
                if skipped:
                    log.info("ollama 写正文轮跳过 %d 个读取调用", len(skipped))
                calls = [c for c in calls if c["function"]["name"] not in blocked]
                if not calls and skipped and not spoken_of(sink):
                    if on_status:
                        on_status("模型还想重读文件，已拦下；请让它直接写正文。")
                    return
            spoken = "".join(sink.get("content") or [])
            if sink.get("done_reason") == "length":
                if calls:
                    log.info("ollama 截断轮跳过 %d 个工具调用", len(calls))
                return
            if spoken.strip():
                bodies.append(spoken.strip())
            if not calls:
                if not spoken:
                    thought = _thinking_text(sink)
                    if thought:
                        if on_status:
                            on_status("正文是空的，改为显示思考里的一份稿子。")
                        if need_gap:
                            yield "\n"
                        yielded = True
                        yield thought
                elif on_status:
                    on_status(f"回答完成，正文 {len(spoken)} 字。")
                return
            if attempt == 3:
                return
            long_bodies = [b for b in bodies if len(b) >= 100]
            reply_body = max(long_bodies, key=len) if long_bodies else _last_assistant_text(original)
            thread.append({"role": "assistant", "content": spoken or "", "tool_calls": calls})
            for call in calls:
                if _stopped(should_stop):
                    return
                fn = call["function"]
                notes: list = []
                result = _run_tool(
                    fn["name"], fn.get("arguments") or {}, on_status,
                    should_stop=should_stop, budget=budget,
                    dirs=True, article_dir=article_dir,
                    reply_body=reply_body, notices=notes,
                )
                for note in notes:
                    _notice(note)
                thread.append({"role": "tool", "name": fn["name"], "content": result})
            if on_status:
                on_status("正在整理结果…")
            if yielded:
                need_gap = True
    except OllamaError as e:
        if yielded or not _no_tool_support(e):
            raise
        if not web:
            raise OllamaError("当前模型不能调用工具，文章只能写在对话里，无法保存成文件。")
        if on_status:
            on_status("当前模型不能调用工具，改为先搜索再回答。")
        query = _last_user_text(original)
        found = web_search(query) if query else "没有可搜索的内容。"
        injected = [{
            "role": "system",
            "content": (
                "联网已开启，但当前模型不能自己调用工具。"
                "下面是根据用户最后一句话搜索到的网页。请据此回答；"
                "结果里没有的内容要说明没查到，不要编造。用简体中文回答。\n\n"
                + found
            ),
        }] + original
        yield from _stream_round(
            base, model, injected, None,
            should_stop, on_response, connect_timeout, read_timeout, {},
        )


def stream_openai_chat(
    base: str,
    api_key: str,
    model: str,
    messages: list,
    should_stop: Optional[Callable[[], bool]] = None,
    on_response: Optional[Callable[[object], None]] = None,
    extra_system: str = "",
    connect_timeout: float = 30.0,
    read_timeout: float = 300.0,
    obedient: bool = True,
) -> Iterator[str]:
    """OpenAI 兼容 /chat/completions 流式输出。不含本机目录工具。"""
    from utils.ollama_intent import OBEY_CHAT
    base = (base or "").strip().rstrip("/")
    if not base:
        raise OllamaError("还没有填写 API 地址。")
    if not model:
        raise OllamaError("还没有选择 API 模型。")
    url = base if base.endswith("/chat/completions") else base + "/chat/completions"
    payload_msgs = []
    sys_txt = (extra_system or "").strip()
    if obedient:
        sys_txt = (OBEY_CHAT + "\n" + sys_txt).strip() if sys_txt else OBEY_CHAT
    if sys_txt:
        payload_msgs.append({"role": "system", "content": sys_txt})
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("system", "user", "assistant"):
            continue
        payload_msgs.append({"role": role, "content": str(m.get("content") or "")})
    headers = {"Content-Type": "application/json"}
    key = (api_key or "").strip()
    if key:
        headers["Authorization"] = "Bearer " + key
    r = None
    try:
        body = {"model": model, "messages": payload_msgs, "stream": True}
        if obedient:
            body["temperature"] = 0.4
        r = requests.post(
            url,
            json=body,
            headers=headers,
            stream=True,
            timeout=(connect_timeout, read_timeout),
        )
        r.raise_for_status()
    except Exception as e:
        try:
            if r is not None:
                r.close()
        except Exception:
            pass
        raise _friendly(e, base, "API")
    if on_response is not None:
        try:
            on_response(r)
        except Exception:
            pass
    try:
        for raw in r.iter_lines(decode_unicode=True):
            if _stopped(should_stop):
                return
            line = (raw or "").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return
            try:
                obj = json.loads(data)
            except Exception:
                continue
            choices = obj.get("choices") or []
            if not choices or not isinstance(choices[0], dict):
                continue
            delta = choices[0].get("delta") or {}
            piece = delta.get("content") or ""
            if piece:
                yield piece
    finally:
        try:
            r.close()
        except Exception:
            pass


def _out_folder(dirs: bool, article_dir: str) -> str:
    if dirs and article_dir:
        try:
            from utils.ollama_dirs import _root
            return _root(article_dir)
        except Exception:
            pass
    return _A.work_dir()


def _web_page_job(url, last, original, dirs, article_dir, seg_ctx, should_stop, on_status, run):
    """抓网页并处理。返回 True=已经处理完；list=把正文放进对话后的新消息；None=没处理。"""
    from utils import ollama_chunk as _CH
    try:
        page = _A.fetch_page(url, should_stop=should_stop, on_status=on_status)
    except Exception as e:
        log.info("ollama 抓取网页失败 %s：%s", url, e)
        return None
    if _stopped(should_stop):
        return True
    text, title = page["text"], page["title"] or "网页正文"
    stem = _A.safe_name(title)
    if _A.is_save_original(last, _CH._TASK_RE):
        folder = _out_folder(dirs, article_dir)
        path = _A.unique_path(folder, stem)
        n = _A.write_text(path, text)
        log.info("ollama 网页原文直接保存 %s 字数=%d", path, n)
        _notice(f"已保存：{path}（{n} 字）")
        if on_status:
            on_status(f"已保存，{n} 字。")
        yield f"已把网页《{title}》的正文完整保存到 {path}，共 {n} 字（打开 {page['pages']} 页）。\n"
        return True
    seg = _CH.segment_chars(seg_ctx, _CH.DEFAULT_CHUNK_RATIO)
    if (
        _CH._TASK_RE.search(last)
        and len(text) > seg
        and not _A.wants_page_nav(last)
    ):
        src = _A.stash_source(stem, text)
        folder = _out_folder(dirs, article_dir)
        job = {
            "root": folder, "source": src, "task": last, "text": _CH.read_text(src),
            "seg_chars": seg, "label": title,
            "output": _A.unique_path(folder, stem + _CH.OUT_SUFFIX),
        }
        yield from run(job)
        return True
    cap = max(seg * 2, 6000)
    body = text if len(text) <= cap else text[:cap] + f"\n…（正文共 {len(text)} 字，这里只给了前 {cap} 字）"
    link_lines = []
    for name, href in (page.get("links") or [])[:40]:
        link_lines.append(f"- {name}\n  {href}")
    link_block = ""
    if link_lines:
        link_block = "\n\n页面上的链接：\n" + "\n".join(link_lines)
    note = (
        f"【程序已打开网页 {url}，标题《{title}》，{len(text)} 字。"
        "正文只出现在对话里，没有存进工作目录。】"
    )
    msgs = [dict(m) for m in original]
    for m in reversed(msgs):
        if m.get("role") == "user":
            m["content"] = f"{m.get('content') or ''}\n\n{note}\n{body}{link_block}"
            break
    return msgs


def _pasted_job(last, original, dirs, article_dir, seg_ctx):
    """用户直接粘贴了一大段文字：太长放不进一轮时，先存成工作文件再分段处理。"""
    from utils import ollama_chunk as _CH
    seg = _CH.segment_chars(seg_ctx, _CH.DEFAULT_CHUNK_RATIO)
    if estimate_tokens(last) * 2 + 2048 <= seg_ctx:
        return None
    paras = [p for p in re.split(r"\n\s*\n", last.strip()) if p.strip()]
    head, tail = (paras[0], paras[-1]) if paras else ("", "")
    task = ""
    for cand in (head, tail):
        if len(cand) <= 300 and (_CH._TASK_RE.search(cand) or _A.is_save_like(cand)):
            task = cand.strip()
            body = last.strip()
            body = body[len(cand):] if body.startswith(cand) else body[: len(body) - len(cand)]
            break
    if not task:
        task = "阅读下面的内容并按用户的要求处理；用户没有说明要求时，逐段概括要点。"
        body = last
    stem = _A.safe_name(body.strip().split("\n", 1)[0][:30], "粘贴内容")
    folder = _out_folder(dirs, article_dir)
    if _A.is_save_original(task, _CH._TASK_RE):
        path = _A.unique_path(folder, stem)
        n = _A.write_text(path, body.strip() + "\n")
        _notice(f"已保存：{path}（{n} 字）")
        return {"saved": path}
    src = _A.stash_source(stem, body.strip() + "\n")
    return {
        "root": folder, "source": src, "task": task, "text": _CH.read_text(src),
        "seg_chars": seg, "label": stem,
        "output": _A.unique_path(folder, stem + _CH.OUT_SUFFIX),
    }


def _continue_task_text(last: str, original: list, dirs: bool, article_dir: str, session_id: str = "") -> str:
    """用户只说「继续」时，找出要接着做的原任务文字（本对话里更早的任务，或进度记录里的任务卡）。"""
    if not dirs:
        return ""
    import os
    try:
        from utils.ollama_progress import (
            _is_continue_message, load_latest_prior, load_progress, progress_path,
        )
    except Exception:
        return ""
    if not _is_continue_message(last):
        return ""
    users = [str(m.get("content") or "").strip() for m in (original or [])
             if isinstance(m, dict) and m.get("role") == "user"]
    for text in reversed(users[:-1]):
        if text and not _is_continue_message(text):
            return text
    if not (article_dir or "").strip():
        return ""
    try:
        data = None
        if session_id:
            data = load_progress(progress_path(os.path.abspath(article_dir), session_id=session_id))
        if not data:
            data, _path = load_latest_prior(os.path.abspath(article_dir))
        card = (data or {}).get("task_card") or {}
        return str(card.get("user_input") or card.get("goal") or (data or {}).get("goal") or "").strip()
    except Exception:
        return ""


def stream_chat(
    base: str,
    model: str,
    messages: list,
    should_stop: Optional[Callable[[], bool]] = None,
    on_response: Optional[Callable[[object], None]] = None,
    connect_timeout: float = 30.0,
    read_timeout: float = 300.0,
    web: bool = False,
    on_status: Optional[Callable[[str], None]] = None,
    dirs: bool = False,
    article_dir: str = "",
    extra_system: str = "",
    on_think: Optional[Callable[[int], None]] = None,
    num_ctx: int = 0,
    on_notice: Optional[Callable[[str], None]] = None,
    think_cap_ratio: float = 0,
    max_continue: int = DEFAULT_MAX_CONTINUE,
    chunk_ratio: float = 0,
    chunk_think: bool = False,
    obedient: bool = True,
    session_id: str = "",
) -> Iterator[str]:
    """POST /api/chat（stream=true），逐段 yield 文本。"""
    base = normalize_base(base)
    _CTX.auto = not num_ctx
    _CTX.num_ctx = clamp_num_ctx(num_ctx) if num_ctx else 0
    _CTX.last_ctx = 0
    _CTX.floor = 0
    _CTX.max_ctx = 0
    _CTX.loaded = 0
    _CTX.on_notice = on_notice
    _CTX.on_status = on_status
    _CTX.dirs = bool(dirs)
    _CTX.truncated = False
    _CTX.think_ratio = think_cap_ratio or 0
    _CTX.temperature = None
    try:
        _CTX.max_continue = max(0, int(max_continue))
    except Exception:
        _CTX.max_continue = DEFAULT_MAX_CONTINUE
    if not model:
        raise OllamaError("没有选择模型。请先刷新并选择一个本地模型。")
    original = [dict(m) for m in messages if isinstance(m, dict)]
    if _CTX.auto:
        _CTX.max_ctx = _A.model_max_ctx(base, model)
        _CTX.loaded = _A.loaded_ctx(base, model)
        log.info("ollama 自动上下文 模型上限=%s 已加载=%s 本次上限=%s",
                 _CTX.max_ctx or "未知", _CTX.loaded or "-", _A.ceiling(_CTX.max_ctx, _CTX.loaded))
    seg_ctx = _CTX.num_ctx or _A.ceiling(_CTX.max_ctx, _CTX.loaded)
    from utils import ollama_chunk as _CH

    def _round(msgs, think, sink):
        return _round_with_continue(
            base, model, msgs, None, should_stop, on_response,
            connect_timeout, read_timeout, sink, think=think, on_status=on_status,
        )

    def _run(job):
        return _CH.run_job(
            job, _round, should_stop=should_stop, on_status=on_status,
            on_notice=_notice, think=False, extra_system=extra_system or "",
        )

    last = _last_user_text(original)
    url = _A.find_url(last)
    if url and _A.chapter_save_plan(last) and not _A.is_convert_to_txt(last):
        if not dirs:
            yield "还没有打开工作目录，无法按目录保存章节。\n"
            return
        if on_status:
            on_status("正在按目录保存章节…")
        try:
            note = _A.save_catalog_chapters(
                article_dir or "", last, should_stop=should_stop, on_status=on_status,
            )
        except Exception as e:
            log.info("ollama 目录保存失败：%s", e)
            if on_status:
                on_status("未完成：没有按目录保存。")
            yield f"未完成：没有按目录保存。{e}\n"
            return
        if note:
            log.info("ollama 意图 catalog_save")
            first = (note.split("\n", 1)[0] or note).strip()
            if on_status:
                on_status(first)
            _notice(first)
            yield note if note.endswith("\n") else note + "\n"
            return
    page_injected = False
    if url:
        handled = yield from _web_page_job(
            url, last, original, dirs, article_dir or "", seg_ctx, should_stop, on_status, _run,
        )
        if handled is True:
            return
        if handled:
            original = handled
            page_injected = True
    elif not url:
        pasted = _pasted_job(last, original, dirs, article_dir or "", seg_ctx)
        if pasted and pasted.get("saved"):
            yield f"已把粘贴的内容完整保存到 {pasted['saved']}。\n"
            return
        if pasted:
            yield from _run(pasted)
            return
    if not dirs and _CH._RESUME_RE.match(last or ""):
        side = _CH._unfinished(_A.work_dir())
        if side:
            yield from _run({"resume": side, "root": _A.work_dir()})
            return
    if _A.is_convert_to_txt(last):
        if not dirs:
            yield "还没有打开工作目录，无法把文件转成 txt。\n"
            return
        from utils.ollama_dirs import export_txt
        try:
            note = export_txt(article_dir or "", last)
        except Exception as e:
            log.info("ollama 转为txt失败：%s", e)
            yield f"没有转成 txt：{e}\n"
            return
        log.info("ollama 意图 convert_txt")
        if on_status:
            on_status("已按原文转成 txt。")
        yield note if note.endswith("\n") else note + "\n"
        return

    route_text = _continue_task_text(last, original, dirs, article_dir or "", session_id) or last
    route = classify_intent(route_text, web=web, dirs=dirs, obedient=obedient)
    _CTX.temperature = route.temperature
    extra = (extra_system or "").strip()
    if route.system:
        extra = (route.system + "\n" + extra).strip() if extra else route.system
    log.info(
        "ollama 意图 %s tools=%s temp=%s",
        route.name, ",".join(route.tools) or "-", route.temperature,
    )
    if on_status and route.label:
        on_status(route.label)

    if dirs and not page_injected:
        from utils.ollama_intent import is_rename_only_task
        job = None
        if not is_rename_only_task(last):
            job = _CH.detect_job(
                original, article_dir or "", seg_ctx,
                chunk_ratio or _CH.DEFAULT_CHUNK_RATIO,
            )
        if job:
            yield from _run(job)
            return

    from utils.ollama_agent import CATALOG_DONE_MARK, run_fncall
    from utils.ollama_dirs import DirBudget
    tools = _tools_named(route.tools)
    if tools:
        folder_note = intent_folder_system(article_dir or "", tuple(route.tools))
        if folder_note:
            extra = (extra + "\n" + folder_note).strip() if extra else folder_note
        tool_fallback = _format_tools_prompt_fallback(tools)
        if tool_fallback:
            extra = (extra + "\n\n" + tool_fallback).strip() if extra else tool_fallback

    thread = list(original)
    if extra:
        thread = [{"role": "system", "content": extra}] + thread
    if not route.tools:
        yield from _round_with_continue(
            base, model, thread, None,
            should_stop, on_response, connect_timeout, read_timeout, {},
            think=route.think, on_think=on_think, on_status=on_status,
        )
        return

    budget = DirBudget()

    goal = ""
    for _m in reversed(original or []):
        if isinstance(_m, dict) and _m.get("role") == "user":
            goal = str(_m.get("content") or "").strip()
            break
    # Preserve the full request: output names and constraints may occur at the end.
    goal = goal.strip()
    progress = None
    if dirs and (article_dir or "").strip():
        try:
            from utils.ollama_progress import ProgressSession
            progress = ProgressSession(
                article_dir, goal=goal,
                session_id=(session_id or "").strip() or None,
                user_text=goal,
            )
            if not (progress.data.get("listed") or []):
                progress.mark_listed()
            try:
                progress.ensure_task_card(goal, listed=progress.data.get("listed") or [])
            except Exception:
                log.info("ollama 任务卡未能写入", exc_info=True)
            if progress.data.get("listed") or progress.task_card():
                progress.save()
        except Exception:
            log.info("ollama 进度记录未能开始", exc_info=True)
            progress = None

    def _round_iter(msgs, toolset, sink, think):
        from utils.ollama_agent import _compact_old_tools
        if getattr(_CTX, "auto", False):
            _auto_ctx(msgs, toolset, think)
        ctx = _ctx_num() or DEFAULT_NUM_CTX
        reserve = min(4096, max(512, ctx // 4))
        def cost():
            return estimate_tokens(json.dumps(msgs, ensure_ascii=False)) + estimate_tokens(json.dumps(toolset, ensure_ascii=False))
        if cost() > ctx - reserve:
            _compact_old_tools(msgs, digest=progress.digest() if progress else "", aggressive=True, progress=progress)
        if cost() > ctx - reserve:
            raise OllamaError("上下文预算不足：未压缩的材料或任务要求过长，已保留进度；需要缩小单次任务。")
        return _round_with_continue(
            base, model, msgs, toolset,
            should_stop, on_response, connect_timeout, read_timeout, sink,
            think=think, on_think=on_think, on_status=on_status,
        )

    def _exec(name, args, spoken=""):
        notes = []
        result = _run_tool(
            name, args or {}, on_status,
            should_stop=should_stop, budget=budget,
            dirs=dirs, article_dir=article_dir or "",
            reply_body=spoken or "", notices=notes,
            progress=progress,
        )
        if progress is not None and name in ("web_search", "web_fetch"):
            try:
                progress.after_tool(name, args or {}, result)
            except Exception:
                log.info("ollama 进度记录失败", exc_info=True)
        for note in notes:
            _notice(note)
        if name == "web_fetch" and dirs and _A.chapter_save_plan(last):
            fetched = str((args or {}).get("url") or "").strip()
            probe = last if _A.find_url(last) else f"{last}\n{fetched}".strip()
            if _A.find_url(probe):
                try:
                    saved = _A.save_catalog_chapters(
                        article_dir or "", probe,
                        should_stop=should_stop, on_status=on_status,
                    )
                except Exception as e:
                    log.info("ollama 目录执行失败：%s", e)
                    saved = f"未完成：没有按目录保存。{e}"
                if saved:
                    return CATALOG_DONE_MARK + "\n" + saved
        return result

    def _stop_review(spoken: str) -> str:
        if progress is not None:
            if progress.success_met():
                return ""
            pending = progress.required_unread()
            if pending:
                return "要求读取的材料尚未完成：" + "、".join(pending[:12]) + "。请继续分段读取并保存笔记。"
            missing = progress.missing_outputs()
            if missing:
                return "成功标准尚未满足：还没有保存 " + "、".join(missing) + "（须有正文）。请继续完成，不要在这里结束。"
        if not _A.chapter_save_plan(last):
            return ""
        return "目录里的章节还没有存进工作目录。请调用 web_fetch 打开目录页，不要在这里结束。"

    try:
        yield from run_fncall(
            round_iter=_round_iter,
            exec_tool=_exec,
            messages=thread,
            tools=tools,
            should_stop=should_stop,
            on_status=on_status,
            think=route.think,
            stop_review=_stop_review,
            progress=progress,
            read_chars=_ctx_char_cap(),
        )
    except OllamaError as e:
        if not _no_tool_support(e):
            raise
        if on_status:
            on_status("当前模型不支持原生 tools 参数，切换为文本工具模式重试…")
        yield from _round_with_continue(
            base, model, thread, None,
            should_stop, on_response, connect_timeout, read_timeout, {},
            think=False, on_status=on_status,
        )