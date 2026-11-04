# utils/ollama_chunk.py
# ---------------------------------------------------------------------------
# 长文分段处理：程序自己把原文按段落/章节切开，一段一段交给模型，
# 每段带上「交接说明」和上一段结尾，结果按顺序追加到一个新文件。
# 中途停下会在输出文件旁边留一个 .progress.json，发「继续」从下一段接着做。
# 纯函数 + 生成器，不碰 Qt。实际请求由 ollama_chat 传进来的 round_fn 发出。
# ---------------------------------------------------------------------------
from __future__ import annotations

import json
import os
import re
import time
from typing import Callable, Iterator, Optional

from utils.logger import get_logger
from utils import ollama_dirs as OD

log = get_logger("deskassist.ollama")

DEFAULT_CHUNK_RATIO = 0.35     # 一段「原文 + 预计输出」最多占上下文的比例
DEFAULT_MAX_CONTINUE = 8       # 回答被长度截断时最多自动续写几次
HANDOVER_MARK = "===交接==="
HANDOVER_CAP = 800
TAIL_CHARS = 400
SIDE_EXT = ".progress.json"
OUT_SUFFIX = "_处理后"

_TASK_RE = re.compile(
    r"改写|润色|翻译|译成|译为|总结|摘要|概括|提炼|校对|纠错|修改|重写|扩写|缩写|精简|"
    r"处理|优化|改成|转成|改为|整理|仿写|续写|改编|降重|删减"
)
_SUMMARY_RE = re.compile(r"总结|摘要|概括|提炼|缩写|精简|大纲|要点|删减")
_RESUME_RE = re.compile(
    r"^\s*(继续|接着|继续处理|接着处理|继续吧|接着来|接着做|继续做|go on|continue)[。.!！~～\s]*$", re.I,
)
_HEAD_RE = re.compile(r"\s*(第[0-9０-９一二三四五六七八九十百千零〇两]+[章节回卷部篇集]|#{1,6}\s)")

CHUNK_SYSTEM = (
    "你在分段处理一篇长文。程序会把原文一段一段交给你，你只处理「当前这一段」。\n"
    "规则：\n"
    "1. 只输出这一段处理后的正文。不要寒暄，不要解释，不要重复上一段已经输出的内容，不要提前写下一段。\n"
    "2. 人物称呼、专有名词、文风要和「交接说明」保持一致；开头要能接上「上一段结尾」。\n"
    f"3. 正文写完后另起一行写 {HANDOVER_MARK}，后面用不超过 300 字写给下一段的交接说明："
    "出现过的人物和称呼、专有名词的写法、文风要点、内容进行到哪里。\n"
    "4. 不要调用任何工具，原文已经在消息里。"
)


# ── 切分 ───────────────────────────────────────────────────
def segment_chars(num_ctx, ratio=DEFAULT_CHUNK_RATIO) -> int:
    """每段原文最多多少字。原文 + 预计同样长的输出 ≤ num_ctx × ratio（token），中文约 1.5 字/token。"""
    try:
        n = int(num_ctx or 0) or 16384
    except Exception:
        n = 16384
    try:
        r = float(ratio or DEFAULT_CHUNK_RATIO)
    except Exception:
        r = DEFAULT_CHUNK_RATIO
    return max(800, int(n * r * 1.5 / 2))


def _sentences(unit: str) -> list:
    out = [s for s in re.findall(r".*?(?:[。！？!?；;…]+[”」』’\"）)]?|$)", unit, flags=re.S) if s]
    return out if "".join(out) == unit else [unit]


def split_source(text: str, limit: int) -> list:
    """按段落/章节切；单个段落太长再按句子切，最后才硬切。所有段拼起来正好等于原文。"""
    text = text or ""
    if not text:
        return []
    limit = max(50, int(limit))
    units = re.findall(r"[^\n]*\n+|[^\n]+$", text)
    if "".join(units) != text:
        units = [text]
    pieces = []
    for u in units:
        if len(u) <= limit:
            pieces.append(u)
            continue
        for s in _sentences(u):
            while len(s) > limit:
                pieces.append(s[:limit])
                s = s[limit:]
            if s:
                pieces.append(s)
    segs, cur = [], ""
    for p in pieces:
        heading = bool(_HEAD_RE.match(p))
        if cur and (len(cur) + len(p) > limit or (heading and len(cur) >= limit * 0.5)):
            segs.append(cur)
            cur = ""
        cur += p
    if cur:
        segs.append(cur)
    return segs


# ── 接缝 ───────────────────────────────────────────────────
def trim_overlap(prev: str, nxt: str, max_check: int = 600, min_len: int = 8) -> str:
    """续写的开头和前文结尾重复时，去掉重复的那一截。"""
    if not prev or not nxt:
        return nxt or ""
    tail = prev[-max_check:]
    lead = len(nxt) - len(nxt.lstrip())
    body = nxt[lead:]
    for n in range(min(len(tail), len(body)), min_len - 1, -1):
        if tail.endswith(body[:n]):
            return body[n:]
    probe = body[:30]
    if len(probe.strip()) >= 20:
        i = tail.find(probe)
        if i >= 0:
            ov = tail[i:]
            if body.startswith(ov):
                return body[len(ov):]
    return nxt


def continue_prompt(written: str) -> str:
    anchor = (written or "")[-TAIL_CHARS:]
    return (
        "你上一条回复因为长度限制被截断了。请从下面这段结尾之后接着写，"
        "不要重复已经写过的内容，不要重新开头，不要解释：\n……" + anchor
    )


# ── 任务识别 ───────────────────────────────────────────────
def _last_user(messages: list) -> str:
    for m in reversed(messages or []):
        if m.get("role") == "user":
            return str(m.get("content") or "")
    return ""


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "").lower()


def read_text(path: str) -> str:
    with open(path, "rb") as f:
        data = f.read()
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(enc).replace("\r\n", "\n")
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace").replace("\r\n", "\n")


def _mentioned_file(root: str, text: str) -> str:
    said = _norm(text)
    best = ""
    for name in os.listdir(root):
        p = os.path.join(root, name)
        stem, ext = os.path.splitext(name)
        if ext.lower() not in OD._TEXT_EXT or not os.path.isfile(p):
            continue
        if _norm(stem) and _norm(stem) in said and len(stem) > len(os.path.splitext(os.path.basename(best))[0] if best else ""):
            best = p
    return best


def _load_side(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _write_side(state: dict) -> None:
    state["updated"] = time.time()
    tmp = state["output"] + SIDE_EXT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, state["output"] + SIDE_EXT)


def _unfinished(root: str, source: str = "") -> Optional[dict]:
    best = None
    for name in os.listdir(root):
        if not name.endswith(SIDE_EXT):
            continue
        d = _load_side(os.path.join(root, name))
        if not d or d.get("finished"):
            continue
        if source and os.path.normcase(d.get("source") or "") != os.path.normcase(source):
            continue
        if best is None or d.get("updated", 0) > best.get("updated", 0):
            best = d
    return best


def output_path(source: str) -> str:
    """新文件名，永远不等于原文件，也不覆盖已有文件。"""
    stem, ext = os.path.splitext(source)
    cand, i = f"{stem}{OUT_SUFFIX}{ext}", 2
    while os.path.exists(cand) or os.path.exists(cand + SIDE_EXT):
        cand = f"{stem}{OUT_SUFFIX}{i}{ext}"
        i += 1
    return cand


def detect_job(messages: list, article_dir: str, num_ctx: int, ratio=DEFAULT_CHUNK_RATIO) -> Optional[dict]:
    """需要分段处理时返回任务说明，否则 None（走普通对话）。"""
    last = _last_user(messages).strip()
    if not last:
        return None
    try:
        root = OD._root(article_dir)
    except Exception:
        return None
    if _RESUME_RE.match(last):
        side = _unfinished(root)
        return {"resume": side, "root": root} if side else None
    if not _TASK_RE.search(last):
        return None
    src = _mentioned_file(root, last)
    if not src:
        return None
    text = read_text(src)
    seg = segment_chars(num_ctx, ratio)
    if len(text) <= seg:
        return None
    side = _unfinished(root, src)
    if side and side.get("task") == last:
        return {"resume": side, "root": root}
    return {"root": root, "source": src, "task": last, "text": text, "seg_chars": seg}


# ── 汇总 ───────────────────────────────────────────────────
def final_note(state: dict) -> str:
    src_n = int(state.get("source_chars") or 0)
    out_n = int(state.get("out_chars") or 0)
    note = (
        f"分段处理完成：源文《{os.path.basename(state.get('source') or '')}》{src_n} 字，"
        f"共 {state.get('total')} 段，输出到 {state.get('output')}，输出 {out_n} 字。"
    )
    if not state.get("summary_task") and src_n and out_n < src_n * 0.6:
        note += f"注意：输出只有源文的 {out_n * 100 // max(1, src_n)}%，比预期短很多，可能有段落被省略，请检查。"
    return note


def _parse(text: str):
    idx = text.rfind(HANDOVER_MARK)
    if idx < 0:
        return text.strip("\n"), ""
    return text[:idx].strip("\n").rstrip(), text[idx + len(HANDOVER_MARK):].strip()[:HANDOVER_CAP]


# ── 驱动 ───────────────────────────────────────────────────
def run_job(
    job: dict,
    round_fn: Callable[[list, Optional[bool], dict], Iterator[str]],
    should_stop: Optional[Callable[[], bool]] = None,
    on_status: Optional[Callable[[str], None]] = None,
    on_notice: Optional[Callable[[str], None]] = None,
    think: bool = False,
    extra_system: str = "",
) -> Iterator[str]:
    """一段一段处理。yield 的是给对话框看的进度行，正文写进输出文件。"""
    def status(t):
        if on_status:
            on_status(t)

    def notice(t):
        if on_notice:
            on_notice(t)

    def stopped():
        return should_stop is not None and bool(should_stop())

    if job.get("resume"):
        state = dict(job["resume"])
        src = state["source"]
        if not os.path.isfile(src):
            notice(f"没法继续：原文件已经不在了：{src}")
            return
        text = read_text(src)
        if len(text) != int(state.get("source_chars") or -1):
            notice("没法继续：原文件在中途被改过，长度对不上。请重新发出处理要求。")
            return
        segs = split_source(text, int(state["seg_chars"]))
        state["total"] = len(segs)
        notice(
            f"继续分段处理《{os.path.basename(src)}》：已完成 {state['done']}/{state['total']} 段，"
            f"从第 {state['done'] + 1} 段接着做，输出到 {state['output']}。"
        )
    else:
        src, text = job["source"], job["text"]
        segs = split_source(text, job["seg_chars"])
        state = {
            "version": 1, "source": src, "source_chars": len(text), "task": job["task"],
            "seg_chars": job["seg_chars"], "total": len(segs), "done": 0,
            "output": job.get("output") or output_path(src), "out_chars": 0, "handover": "", "tail": "",
            "summary_task": bool(_SUMMARY_RE.search(job["task"])), "finished": False,
            "label": job.get("label") or "",
        }
        _write_side(state)
        notice(
            "内容较长，已自动分段处理。"
            f"源文《{job.get('label') or os.path.basename(src)}》{len(text)} 字，分成 {len(segs)} 段"
            f"（每段不超过 {job['seg_chars']} 字），结果按顺序写到新文件 {state['output']}，原文件不改。"
        )
    total = state["total"]
    name = state.get("label") or os.path.basename(src)
    system = CHUNK_SYSTEM + (("\n" + extra_system.strip()) if (extra_system or "").strip() else "")
    if state.get("summary_task"):
        system += "\n这是摘要类任务：每段只写这一段的要点，不用和原文一样长。"
    log.info("ollama 分段 开始 %s 共%d段 从第%d段 输出=%s", src, total, state["done"] + 1, state["output"])
    while state["done"] < total:
        k = state["done"]
        if stopped():
            break
        status(f"分段处理：第 {k + 1}/{total} 段…")
        parts = [f"任务：{state['task']}", f"原文《{name}》共 {total} 段，这是第 {k + 1}/{total} 段。"]
        if state.get("handover"):
            parts.append("【交接说明】\n" + state["handover"])
        if state.get("tail"):
            parts.append("【上一段处理结果的结尾（只用来衔接，不要重复）】\n……" + state["tail"])
        parts.append(f"【第 {k + 1} 段原文】\n" + segs[k])
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(parts)}]
        text_out, sink = "", {}
        for flag in ((None if think else False), False):
            sink = {}
            text_out = "".join(round_fn(msgs, flag, sink))
            if stopped() or text_out.strip() or flag is False:
                break
            status("这一段只有思考没有正文，关掉思考再写一次。")
        if stopped():
            break
        if sink.get("incomplete"):
            notice(
                f"第 {k + 1}/{total} 段已自动关闭思考、放大上下文并续写 {sink.get('continued', 0)} 次，"
                "仍没写完，这一段没有写入。已完成的段落都在输出文件里；发送「继续」会从这一段重试。"
            )
            _write_side(state)
            return
        body, handover = _parse(text_out)
        if not body.strip():
            notice(f"第 {k + 1}/{total} 段模型没有给出正文，已停下。发送「继续」可以重试这一段。")
            _write_side(state)
            return
        nl = len(segs[k]) - len(segs[k].rstrip("\n"))
        chunk = body + ("\n" * max(1, min(2, nl)) if k + 1 < total else "\n")
        with open(state["output"], "w" if k == 0 else "a", encoding="utf-8") as f:
            f.write(chunk)
        state["out_chars"] = len(read_text(state["output"]))
        state["done"] = k + 1
        state["tail"] = body[-TAIL_CHARS:]
        if handover:
            state["handover"] = handover
        _write_side(state)
        log.info("ollama 分段 第%d/%d段 原文=%d 输出=%d 交接=%d", k + 1, total, len(segs[k]), len(body), len(handover))
        status(f"分段处理：第 {k + 1}/{total} 段完成（累计输出 {state['out_chars']} 字）")
        yield f"第 {k + 1}/{total} 段完成，写入 {len(body)} 字。\n"
    if state["done"] < total:
        _write_side(state)
        notice(
            f"已停在第 {state['done']}/{total} 段：前 {state['done']} 段已写入 {state['output']}。"
            "发送「继续」从下一段接着处理。"
        )
        return
    state["finished"] = True
    _write_side(state)
    note = final_note(state)
    notice(note)
    status(f"分段处理完成，共 {total} 段，输出 {state['out_chars']} 字。")
