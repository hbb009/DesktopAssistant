# utils/ollama_dirs.py
# 文章只保存到用户指定的一个文件夹。模型不能改这个文件夹以外的路径。

from __future__ import annotations

import os
import re
from typing import Callable, Optional

from utils.logger import get_logger

log = get_logger("deskassist.ollama")

TOOL_NAMES = frozenset({"list_articles", "read_article", "save_article"})

_READ_CAP = 60000          # 字节；超出只读前面一部分，并记为「只读了一部分」
_WRITE_CAP = 100000
_LIST_CAP = 200
_READ_BUDGET = 12
_WRITE_BUDGET = 6
_TEXT_EXT = {".md", ".txt", ".markdown"}
_ABS_RE = re.compile(r"^[A-Za-z]:[\\/]")
_BAD_NAME = re.compile(r'[<>:"|?*\x00-\x1f]')

_APP_ROOT = os.path.normcase(os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))


# 这一段对话里只读了一部分的文件（normcase 路径 → 读到的字数）。整篇覆盖会被拒绝。
_PARTIAL_READS: dict = {}
# content 写这些时，表示「把回复正文原样存进去」，不用再写一遍全文。
_SAME_AS_REPLY = {"同上", "见上文", "上文", "正文", "（同上）", "(同上)", "同上文", "回复正文"}


def clear_partial_reads() -> None:
    """新对话 / 清屏时调用。"""
    _PARTIAL_READS.clear()


def partial_reads() -> dict:
    return dict(_PARTIAL_READS)


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _note(notices, text: str) -> None:
    if notices is not None and text:
        notices.append(text)


class DirBudget:
    def __init__(self):
        self.reads = 0
        self.writes = 0


def status_text(name: str, args: dict) -> str:
    args = args or {}
    fn = str(args.get("filename") or args.get("name") or args.get("path") or "").strip()
    if name == "save_article":
        return f"正在保存文章…{fn}" if fn else "正在保存文章…"
    if name == "read_article":
        return f"正在阅读…{fn}" if fn else "正在阅读文章…"
    if name == "list_articles":
        return "正在查看文章目录…"
    return "正在处理…"


def run_dir_tool(
    name: str,
    args: dict,
    article_dir: str = "",
    should_stop: Optional[Callable[[], bool]] = None,
    budget: Optional[DirBudget] = None,
    reply_body: str = "",
    read_chars: int = 0,
    notices: Optional[list] = None,
) -> str:
    """reply_body：模型这一轮回复的正文；save_article 不带 content 时就存它。
    read_chars：read_article 最多交给模型多少字（按上下文长度算）。
    notices：要在对话里长期显示给用户的提示，追加到这个列表。"""
    try:
        return _run(
            name, args or {}, article_dir, should_stop, budget or DirBudget(),
            reply_body or "", int(read_chars or 0), notices,
        )
    except Exception as e:
        log.warning("ollama article %s: %s", name, e)
        msg = str(e).strip() or "没有完成。"
        if msg.startswith(("没有", "用户", "已停止", "还没有", "只能", "保存目录", "这个位置")):
            return msg
        return f"没有完成：{msg}"


def _run(name, args, article_dir, should_stop, budget: DirBudget,
         reply_body: str = "", read_chars: int = 0, notices=None) -> str:
    if _stopped(should_stop):
        return "已停止。"
    root = _root(article_dir)
    if name == "list_articles":
        return _meter_read(budget, lambda: _list_articles(root))
    if name == "read_article":
        path = _inside(root, args.get("filename") or args.get("name") or args.get("path"))
        return _meter_read(budget, lambda: _read_article(path, read_chars))
    if name == "save_article":
        path = _inside(root, args.get("filename") or args.get("name") or args.get("path"))
        content = args.get("content")
        if content is None:
            content = args.get("text") or args.get("contents") or ""
        content = str(content)
        from_reply = False
        if content.strip() in _SAME_AS_REPLY or not content.strip():
            if (reply_body or "").strip():
                content = reply_body.strip() + "\n"
                from_reply = True
            else:
                # 绝不能把「同上」或空内容写进文件（会把原文覆盖成两个字）。
                return (
                    "没有保存：content 是空的或只写了「同上」，但这一轮回复里没有正文可存。"
                    "请先把完整正文直接写在回复里，然后再调用 save_article（只写 filename）。"
                )
        return _save_article(
            root, path, content, should_stop, budget,
            mode=_mode(args), reply_body=reply_body, notices=notices, from_reply=from_reply,
        )
    return f"没有这个工具：{name}"


def _mode(args: dict) -> str:
    raw = str(args.get("mode") or "").strip().lower()
    if raw in ("append", "a", "add", "追加", "续写"):
        return "append"
    flag = args.get("append")
    if isinstance(flag, str):
        flag = flag.strip().lower() in ("1", "true", "yes", "on")
    return "append" if flag else "overwrite"


def _meter_read(budget: DirBudget, fn) -> str:
    budget.reads += 1
    if budget.reads > _READ_BUDGET:
        return "查看次数够多了，请根据已经拿到的结果回答。"
    return fn()


def _stopped(should_stop) -> bool:
    return bool(should_stop and should_stop())


def _root(article_dir: str) -> str:
    raw = str(article_dir or "").strip().strip('"')
    if not raw:
        raise ValueError("还没有指定保存目录。请让用户在页面上选择文件夹。")
    if not _ABS_RE.match(raw):
        raise ValueError("保存目录必须是绝对路径。请让用户在页面上重新选择。")
    root = os.path.normpath(os.path.abspath(raw))
    if _bad_root(root):
        raise ValueError("这个位置不能当作文章目录。请另选一个普通文件夹。")
    if not os.path.isdir(root):
        raise ValueError(f"保存目录不存在：{root}")
    return root


def _bad_root(path: str) -> bool:
    rest = os.path.splitdrive(path)[1]
    if rest in ("", "\\", "/"):
        return True
    norm = os.path.normcase(os.path.realpath(path))
    win = os.path.normcase(os.path.realpath(os.environ.get("SystemRoot") or r"C:\Windows"))
    app = os.path.normcase(os.path.realpath(_APP_ROOT))
    for base in (win, app):
        if norm == base or norm.startswith(base + os.sep):
            return True
    return False


def _inside(root: str, filename) -> str:
    raw = str(filename or "").strip().strip('"')
    raw = raw.replace("/", os.sep).replace("\\", os.sep)
    if not raw or os.path.isabs(raw) or raw.startswith("\\\\") or ":" in raw:
        raise ValueError("只能填写文件名，例如「春日随笔.md」，不要写盘符。")
    parts = [p for p in raw.split(os.sep) if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise ValueError("文件名不能包含上一级目录。")
    clean = []
    for part in parts:
        if _BAD_NAME.search(part) or part.endswith((" ", ".")):
            raise ValueError(f"文件名不合法：{part}")
        clean.append(part)
    last = clean[-1]
    base, ext = os.path.splitext(last)
    if not base.strip():
        raise ValueError("文件名是空的。")
    if ext.lower() not in _TEXT_EXT and ext != "":
        raise ValueError("只能保存 .md 或 .txt 文章。")
    if ext == "":
        clean[-1] = base + ".md"
    dest = os.path.abspath(os.path.join(root, *clean))
    if not _contained(root, dest):
        raise ValueError("文件不能放到指定目录之外。")
    return dest


def _contained(root: str, dest: str) -> bool:
    root_real = os.path.normcase(os.path.realpath(root))
    dest_real = os.path.normcase(os.path.realpath(dest))
    return dest_real == root_real or dest_real.startswith(root_real + os.sep)


def _list_articles(root: str) -> str:
    try:
        names = os.listdir(root)
    except PermissionError:
        return f"没有权限查看：{root}"
    except OSError as e:
        return f"没有看成：{root}（{e}）"
    rows = []
    for name in names:
        path = os.path.join(root, name)
        if not _contained(root, path):
            continue
        if os.path.isdir(path):
            rows.append((0, name.casefold(), f"[文件夹] {name}"))
            continue
        if os.path.isfile(path):
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            rows.append((1, name.casefold(), f"[文件] {name}  {_fmt_size(size)}"))
    rows.sort()
    lines = [root, f"共 {len(rows)} 项"]
    lines.extend(text for _a, _b, text in rows[:_LIST_CAP])
    if len(rows) > _LIST_CAP:
        lines.append(f"还有 {len(rows) - _LIST_CAP} 项没有列出。")
    return "\n".join(lines)


def _fmt_size(n: int) -> str:
    n = max(0, int(n or 0))
    if n < 1024:
        return f"{n} B"
    val = float(n)
    for unit in ("KB", "MB", "GB"):
        val /= 1024.0
        if val < 1024 or unit == "GB":
            return f"{val:.1f} {unit}"
    return f"{n} B"


def _read_article(path: str, read_chars: int = 0) -> str:
    if os.path.isdir(path):
        return "这是文件夹，请改读其中的文章文件。"
    if not os.path.isfile(path):
        return f"找不到这篇文章：{os.path.basename(path)}"
    ext = os.path.splitext(path)[1].lower()
    if ext not in _TEXT_EXT:
        return "只能阅读 .md 或 .txt 文章。"
    try:
        with open(path, "rb") as f:
            data = f.read(_READ_CAP + 1)
    except PermissionError:
        return f"没有权限读取：{os.path.basename(path)}"
    except OSError as e:
        return f"没有读成：{os.path.basename(path)}（{e}）"
    if b"\x00" in data[:4096]:
        return "这不是文本文章，没有读取。"
    truncated = len(data) > _READ_CAP
    data = data[:_READ_CAP]
    text = _decode(data, truncated)
    if read_chars and read_chars > 0 and len(text) > read_chars:
        text = text[:read_chars]
        truncated = True
    key = _key(path)
    if truncated:
        _PARTIAL_READS[key] = len(text)
        log.info("ollama article partial read %s chars=%d", path, len(text))
        text += (
            f"\n…（文件更大，只读了前面 {len(text)} 字。不要整篇覆盖这个文件，"
            "否则没读到的部分会丢失；要续写请用 save_article 的 mode=append，或另存为新文件名。）"
        )
    else:
        _PARTIAL_READS.pop(key, None)
    return text


def _decode(data: bytes, cut: bool) -> str:
    """截断处可能落在多字节字符中间：先去掉末尾半个字再试，避免误判成 GBK 乱码。"""
    tries = [data]
    if cut:
        tries += [data[:-n] for n in (1, 2, 3) if len(data) > n]
    for enc in ("utf-8-sig", "utf-8"):
        for chunk in tries:
            try:
                return chunk.decode(enc)
            except UnicodeDecodeError:
                continue
    for chunk in tries:
        try:
            return chunk.decode("gb18030")
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _save_article(root, path, content, should_stop, budget: DirBudget,
                  mode: str = "overwrite", reply_body: str = "", notices=None,
                  from_reply: bool = False) -> str:
    if _stopped(should_stop):
        return "已停止。"
    if len(content) > _WRITE_CAP:
        return f"文章太长（{len(content)} 字），没有保存。"
    if not content.strip():
        return "文章是空的，没有保存。"
    parent = os.path.dirname(path)
    if not _contained(root, parent if parent else root):
        return "文件不能放到指定目录之外。"
    if os.path.isdir(path):
        return "这个名字已经是文件夹，没有保存。"
    append = mode == "append"
    key = _key(path)
    if not append and key in _PARTIAL_READS and os.path.isfile(path):
        msg = (
            f"已拒绝覆盖：{os.path.basename(path)} 之前只读到前面 {_PARTIAL_READS[key]} 字，"
            "整篇覆盖会丢掉没读到的部分。请改用 mode=append 追加到文件末尾，或换一个新文件名保存。"
        )
        log.info("ollama article overwrite refused %s", path)
        _note(notices, msg)
        return msg
    if budget.writes >= _WRITE_BUDGET:
        return "这一轮保存次数已用完，请先把结果告诉用户。"
    budget.writes += 1
    os.makedirs(parent, exist_ok=True)
    with open(path, "a" if append else "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    written = len(content.replace("\r\n", "\n"))
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            total = len(f.read())
    except Exception:
        total = -1
    log.info("ollama article save %s mode=%s chars=%d total=%d", path, mode, written, total)
    if append:
        msg = f"已保存：{path}（追加 {written} 字，文件现有 {total} 字）"
    else:
        msg = f"已保存：{path}（{written} 字）"
        if total >= 0 and total != written:
            msg += f"\n注意：写入 {written} 字，但文件里读回 {total} 字，请检查文件。"
    body_n = len((reply_body or "").strip())
    if not append and not from_reply and body_n >= 200 and written < 0.9 * body_n:
        warn = (
            f"注意：保存的内容只有 {written} 字，而回复正文有 {body_n} 字（不到 90%），"
            f"文件可能不完整：{path}"
        )
        msg += "\n" + warn
    _note(notices, msg)
    return msg
