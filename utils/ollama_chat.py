# utils/ollama_chat.py
# ---------------------------------------------------------------------------
# 本地 Ollama 对话：模型列表 + /api/chat 流式输出（纯函数，不碰 Qt）
# 参考 D:\API\utils\ollama_client.py，地址可配置、错误转成中文提示。
# ---------------------------------------------------------------------------

from __future__ import annotations

import base64
import codecs
import json
import re
from datetime import date
from html import unescape
from typing import Callable, Iterator, List, Optional
from urllib.parse import parse_qs, urlparse

import requests

from utils.logger import get_logger

log = get_logger("deskassist.ollama")

DEFAULT_BASE = "http://127.0.0.1:11434"


class OllamaError(Exception):
    """带中文说明的 Ollama 错误。"""


def normalize_base(base: str) -> str:
    s = (base or "").strip().rstrip("/")
    if not s:
        return DEFAULT_BASE
    if not s.startswith(("http://", "https://")):
        s = "http://" + s
    return s


def _friendly(exc: Exception, base: str) -> OllamaError:
    if isinstance(exc, OllamaError):
        return exc
    if isinstance(exc, requests.ConnectionError):
        return OllamaError(f"连接不上 Ollama（{base}）。请确认 Ollama 已启动，地址和端口填写正确。")
    if isinstance(exc, requests.Timeout):
        return OllamaError(f"Ollama 响应超时（{base}）。模型可能正在加载，请稍后再试。")
    if isinstance(exc, requests.HTTPError):
        resp = exc.response
        detail = ""
        try:
            detail = (resp.json() or {}).get("error", "") if resp is not None else ""
        except Exception:
            detail = (resp.text or "")[:200] if resp is not None else ""
        code = resp.status_code if resp is not None else "?"
        if code == 404:
            return OllamaError(f"模型不存在或接口不可用（HTTP 404）：{detail or '请刷新模型列表'}")
        return OllamaError(f"Ollama 返回错误（HTTP {code}）：{detail or '未知错误'}")
    return OllamaError(f"请求 Ollama 出错：{exc}")


def list_models(base: str = DEFAULT_BASE, timeout: float = 3.0) -> List[str]:
    """GET /api/tags → 模型名列表。失败抛 OllamaError。"""
    base = normalize_base(base)
    try:
        r = requests.get(f"{base}/api/tags", timeout=timeout)
        r.raise_for_status()
        models = (r.json() or {}).get("models", []) or []
    except Exception as e:
        raise _friendly(e, base)
    names = [str(m.get("name") or m.get("model") or "") for m in models if isinstance(m, dict)]
    return sorted(n for n in names if n)


_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# 只有「联网」打开时才放进 /api/chat。关闭时不声明工具，模型无法发起访问。
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

_TOOL_OPEN = "<tool_call>"
_TOOL_CLOSE = "</tool_call>"
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


def web_fetch(url: str, limit: int = 6000) -> str:
    """打开网页并抽出正文。只接受 http/https。"""
    url = (url or "").strip()
    if url.startswith("www."):
        url = "https://" + url
    if not url.startswith(("http://", "https://")):
        return "只能打开 http 或 https 地址。"
    try:
        r = requests.get(
            url,
            headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"},
            timeout=15,
            stream=True,
            allow_redirects=True,
        )
        r.raise_for_status()
        ctype = (r.headers.get("Content-Type") or "").lower()
        if ctype and not any(k in ctype for k in ("text/", "html", "xml", "json", "javascript")):
            return f"这个地址不是网页正文（{ctype.split(';')[0]}），没有读取。"
        buf = b""
        for chunk in r.iter_content(8192):
            if not chunk:
                continue
            buf += chunk
            if len(buf) >= 1_200_000:
                break
    except Exception as e:
        return f"打开网页失败：{e}"
    finally:
        try:
            r.close()
        except Exception:
            pass
    if b"\x00" in buf[:200]:
        return "这个地址不是文本网页，没有读取。"
    enc = r.encoding or "utf-8"
    text = _html_text(buf.decode(enc, "replace"))
    if not text:
        return "页面没有读到正文。"
    if len(text) > limit:
        text = text[:limit] + "\n…（后面已截断）"
    return text


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


def _run_tool(name: str, args: dict, on_status: Optional[Callable[[str], None]]) -> str:
    if name == "web_search":
        query = str(args.get("query") or args.get("q") or "").strip()
        if on_status:
            on_status(f"正在搜索：{query or '…'}")
        return web_search(query)[:5000]
    if name == "web_fetch":
        url = str(args.get("url") or "").strip()
        if on_status:
            on_status("正在打开网页…")
        return web_fetch(url)[:6000]
    return f"没有这个工具：{name}"


def _last_user_text(messages: list) -> str:
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            return str(m.get("content") or "").strip()
    return ""


def _no_tool_support(exc: Exception) -> bool:
    return "tool" in str(exc).lower()


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
        if not isinstance(obj, dict) or not obj.get("name"):
            continue
        args = obj.get("arguments")
        if args is None:
            args = obj.get("parameters") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except Exception:
                args = {"query": args}
        if not isinstance(args, dict):
            args = {}
        out.append({"type": "function", "function": {"name": str(obj["name"]).strip(), "arguments": args}})
    return out


def _parse_text_tool_calls(text: str) -> list:
    """识别写在正文里的工具调用。当前模型不会走接口字段，而是输出：

    <tool_call>
    <function=web_search>
    <parameter=query>
    关键词
    </parameter>
    </function>
    </tool_call>
    """
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
    """流式正文里先扣下 <tool_call>，不显示给用户。"""

    def __init__(self):
        self.buf = ""
        self.calls: list = []

    def feed(self, piece: str) -> str:
        if not piece:
            return ""
        self.buf += piece
        out = []
        while True:
            low = self.buf.lower()
            start = low.find(_TOOL_OPEN)
            if start < 0:
                keep = _tag_prefix_suffix(self.buf, _TOOL_OPEN)
                emit = self.buf[:-len(keep)] if keep else self.buf
                self.buf = keep
                if emit:
                    out.append(emit)
                break
            if start:
                out.append(self.buf[:start])
            end = low.find(_TOOL_CLOSE, start + len(_TOOL_OPEN))
            if end < 0:
                self.buf = self.buf[start:]
                break
            end += len(_TOOL_CLOSE)
            self.calls.extend(_parse_text_tool_calls(self.buf[start:end]))
            self.buf = self.buf[end:]
        return "".join(out)

    def finish(self) -> str:
        rest = self.buf
        self.buf = ""
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
) -> Iterator[str]:
    """一轮 /api/chat。正文逐段 yield；全文、思考和工具调用写入 sink。"""
    sink["content"] = []
    sink["thinking"] = []
    sink["calls"] = []
    sink["text_calls"] = []
    text_filter = _ToolTextFilter()
    payload = {"model": model, "messages": messages, "stream": True}
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
        r.raise_for_status()
    except Exception as e:
        try:
            if r is not None:
                r.close()
        except Exception:
            pass
        raise _friendly(e, base)
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
                if thinking:
                    sink["thinking"].append(thinking)
                content = msg.get("content") or ""
                if content:
                    visible = text_filter.feed(content)
                    if visible:
                        sink["content"].append(visible)
                        yield visible
                if obj.get("done"):
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
    tail = text_filter.finish()
    sink["text_calls"] = list(text_filter.calls)
    if tail:
        sink["content"].append(tail)
        yield tail


def _stopped(should_stop: Optional[Callable[[], bool]]) -> bool:
    return should_stop is not None and bool(should_stop())


def _round_calls(sink: dict) -> list:
    native = [_normalize_call(c) for c in (sink.get("calls") or []) if isinstance(c, dict)]
    text_calls = [_normalize_call(c) for c in (sink.get("text_calls") or []) if isinstance(c, dict)]
    calls = text_calls or native
    return [c for c in calls if (c.get("function") or {}).get("name")]


def _thinking_text(sink: dict) -> str:
    """模型把整段话写进思考、正文为空时，拿出来给用户看。"""
    raw = "".join(sink.get("thinking") or []).strip()
    if not raw:
        return ""
    filt = _ToolTextFilter()
    text = (filt.feed(raw) + filt.finish()).strip()
    if len(text) > 1800:
        text = text[-1800:].lstrip()
    return text


def _log_round(kind: str, sink: dict, calls: list) -> None:
    log.info(
        "ollama %s 正文=%d 思考=%d 工具=%d",
        kind,
        len("".join(sink.get("content") or [])),
        len("".join(sink.get("thinking") or [])),
        len(calls),
    )


def stream_chat(
    base: str,
    model: str,
    messages: list,
    should_stop: Optional[Callable[[], bool]] = None,
    on_response: Optional[Callable[[object], None]] = None,
    connect_timeout: float = 5.0,
    read_timeout: float = 300.0,
    web: bool = False,
    on_status: Optional[Callable[[str], None]] = None,
) -> Iterator[str]:
    """POST /api/chat（stream=true），逐段 yield 文本。

    should_stop() 返回 True 时立即关闭连接并结束。
    on_response(resp) 拿到响应对象，便于其他线程 close() 打断阻塞读。
    web 为真时声明 web_search / web_fetch，模型可以搜索并打开网页。
    模型不支持工具时，改为先搜索用户最后一句话，再把结果交给模型。
    """
    base = normalize_base(base)
    if not model:
        raise OllamaError("没有选择模型。请先刷新并选择一个本地模型。")
    original = [dict(m) for m in messages if isinstance(m, dict)]
    if not web:
        yield from _stream_round(
            base, model, original, None,
            should_stop, on_response, connect_timeout, read_timeout, {},
        )
        return

    thread = [{
        "role": "system",
        "content": _WEB_SYSTEM + f"今天的公历日期是 {date.today().isoformat()}。",
    }] + original
    yielded = False
    try:
        result_text = ""
        for _attempt in range(2):
            if _stopped(should_stop):
                return
            sink: dict = {}
            for piece in _stream_round(
                base, model, thread, _WEB_TOOLS,
                should_stop, on_response, connect_timeout, read_timeout, sink,
            ):
                yielded = True
                yield piece
            if _stopped(should_stop):
                return
            calls = _round_calls(sink)
            _log_round("提问", sink, calls)
            spoken = "".join(sink.get("content") or [])
            if not calls:
                if not spoken:
                    thought = _thinking_text(sink)
                    if thought:
                        yielded = True
                        yield thought
                return
            chunks = []
            for call in calls:
                if _stopped(should_stop):
                    return
                fn = call["function"]
                result = _run_tool(fn["name"], fn.get("arguments") or {}, on_status)
                chunks.append(f"【{fn['name']}】\n{result}")
            result_text = "\n\n".join(chunks)
            thread.append({"role": "assistant", "content": spoken or "我先查一下。"})
            thread.append({
                "role": "user",
                "content": (
                    "下面是联网结果。请直接用简体中文回答，不要再调用工具。\n\n"
                    + result_text
                ),
            })
            if on_status:
                on_status("正在整理联网结果…")
            # 关掉思考，避免答案只写在思考里、正文为空。
            answer: dict = {}
            for piece in _stream_round(
                base, model, thread, None,
                should_stop, on_response, connect_timeout, read_timeout, answer,
                think=False,
            ):
                yielded = True
                yield piece
            if _stopped(should_stop):
                return
            again = _round_calls(answer)
            _log_round("作答", answer, again)
            answer_text = "".join(answer.get("content") or [])
            if answer_text and not again:
                return
            if again and _attempt == 0:
                continue
            thought = _thinking_text(answer)
            if thought:
                yielded = True
                yield thought
                return
            if result_text:
                yielded = True
                yield "联网查到这些内容：\n\n" + result_text[:2000]
            return
    except OllamaError as e:
        if yielded or not _no_tool_support(e):
            raise
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
