# utils/ollama_dirs.py
# 文章只保存到用户指定的一个文件夹。模型不能改这个文件夹以外的路径。

from __future__ import annotations

import os
import re
from typing import Callable, Optional

from utils.logger import get_logger

log = get_logger("deskassist.ollama")

TOOL_NAMES = frozenset({"list_articles", "read_article", "save_article", "save_notes", "read_notes", "read_unread_batch", "rename_article"})

# 单次读写大小上限仍然保留（超长文件分段读）；次数预算不再用来截停任务。
# 进度由 utils.ollama_progress 落盘，哪一步超出就从记录接着做。
_READ_CAP = 2_000_000      # 字节；超长文件只先读一段，可用 start 继续
_WRITE_CAP = 2_000_000     # 字
_LIST_CAP = 2000
# 保留计数器字段，不再作为截停条件。
_READ_BUDGET = 0
_WRITE_BUDGET = 0
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
        return f"写入 {fn}" if fn else "写入"
    if name in ("read_article", "read_unread_batch"):
        return f"读取 {fn}" if fn else "读取"
    if name == "list_articles":
        return "查看目录"
    if name == "read_notes":
        return "读取笔记"
    if name == "save_notes":
        return "记录笔记"
    if name == "rename_article":
        newn = str(args.get("new_filename") or args.get("new_name") or args.get("to") or "").strip()
        if fn and newn:
            return f"重命名 {fn} → {newn}"
        return f"重命名 {fn}" if fn else "重命名"
    return "执行中"


_NUM_SPLIT = re.compile(r"(\d+)")
# 读取结果第一行标明实际读到的文件（按 index 读时模型看不出是哪一章）。
READ_HEADER = "【本次读取的文件："


def natural_key(rel: str):
    """自然顺序：第2章 排在 第10章 前面，清单编号与章节顺序一致。逐级路径比较。"""
    parts = []
    for seg in str(rel or "").replace("\\", "/").split("/"):
        bits = _NUM_SPLIT.split(seg.casefold())
        parts.append(tuple((0, int(b), "") if b.isdigit() else (1, 0, b) for b in bits if b != ""))
    return tuple(parts)


def as_rel_in_root(root: str, raw) -> str:
    """模型把工作目录本身、或其中文件的绝对路径传来时，换成相对路径（工作目录本身为空串）。
    目录外的绝对路径原样返回，之后照旧被拒绝。只用于查看/读取，不放宽写入。"""
    s = str(raw or "").strip().strip('"').strip("'").strip()
    if not s or not root:
        return s
    norm = s.replace("/", os.sep)
    if _ABS_RE.match(s) or os.path.isabs(norm):
        full = os.path.normpath(os.path.abspath(norm))
        if _contained(root, full):
            return _rel_posix(root, full)
        return s
    bare = s.strip("/\\")
    if bare and bare.casefold() == os.path.basename(os.path.normpath(root)).casefold():
        if not os.path.exists(os.path.join(root, bare)):
            return ""
    return s


def _normalize_path_args(name: str, args: dict, article_dir: str) -> dict:
    if name not in ("list_articles", "read_article", "read_unread_batch"):
        return args
    try:
        root = _root(article_dir)
    except Exception:
        return args
    out = dict(args or {})
    for key in ("filename", "name", "path", "folder"):
        val = out.get(key)
        if not isinstance(val, str) or not val.strip():
            continue
        fixed = as_rel_in_root(root, val)
        if fixed == val:
            continue
        if fixed == "" and name != "list_articles":
            # 读「工作目录本身」：保留原值，由读取逻辑返回清单或报错
            continue
        out[key] = fixed
    return out


def _with_read_header(rel: str, text: str, listed=None) -> str:
    t = text or ""
    head = t.lstrip()
    if not rel:
        return t
    try:
        from utils.ollama_progress import _READ_FAIL
    except Exception:
        _READ_FAIL = ()
    if head.startswith(tuple(_READ_FAIL) + ("找不到", "工作目录：", "文件夹：", "已停止")):
        return t
    idx = ""
    norm = [str(x).replace("\\", "/") for x in (listed or [])]
    if rel in norm:
        idx = "（清单 index=%d）" % (norm.index(rel) + 1)
    return "%s%s】%s\n%s" % (READ_HEADER, rel, idx, t)


def run_dir_tool(
    name: str,
    args: dict,
    article_dir: str = "",
    should_stop: Optional[Callable[[], bool]] = None,
    budget: Optional[DirBudget] = None,
    reply_body: str = "",
    read_chars: int = 0,
    notices: Optional[list] = None,
    progress=None,
) -> str:
    """reply_body：模型这一轮回复的正文；save_article 不带 content 时就存它。
    read_chars：read_article 最多交给模型多少字（按上下文长度算）。
    notices：要在对话里长期显示给用户的提示，追加到这个列表。
    progress：进度会话；提供时由程序记下每一步，并决定已处理文件是否回全文。"""
    args = _normalize_path_args(name, args or {}, article_dir)
    try:
        result = _run(
            name, args or {}, article_dir, should_stop, budget or DirBudget(),
            reply_body or "", int(read_chars or 0), notices, progress,
        )
    except Exception as e:
        log.warning("ollama article %s: %s", name, e)
        msg = str(e).strip() or "没有完成。"
        if msg.startswith(("没有", "用户", "已停止", "还没有", "只能", "保存目录", "这个位置")):
            result = msg
        else:
            result = f"没有完成：{msg}"
    if progress is not None and name in ("list_articles", "read_article", "read_unread_batch", "save_article", "save_notes", "rename_article"):
        try:
            progress.after_tool(name, args or {}, result, spoken=reply_body or "")
        except Exception:
            log.info("ollama 进度记录失败", exc_info=True)
    return result


def _run(name, args, article_dir, should_stop, budget: DirBudget,
         reply_body: str = "", read_chars: int = 0, notices=None, progress=None) -> str:
    if _stopped(should_stop):
        return "已停止。"
    if name == "read_notes":
        if progress is None:
            return "没有进度记录，无法读取笔记。"
        return progress.read_notes(args.get("start", 0))
    if name == "save_notes":
        if progress is None:
            return "没有进度记录，无法保存笔记。"
        files = args.get("files") if args.get("files") is not None else args.get("filenames")
        if files is None:
            files = args.get("filename")
        content = str(args.get("content") or args.get("text") or args.get("contents") or "")
        return progress.save_notes(content, files=files)
    root = _root(article_dir)
    if _is_rename_only_progress(progress) and name in ("read_article", "read_unread_batch", "save_article"):
        return _RENAME_REFUSE
    if name == "list_articles":
        return _meter_read(budget, lambda: _list_articles(root, args or {}, progress))
    if name in ("read_article", "read_unread_batch"):
        return _read_served(root, args or {}, budget, read_chars, progress)
    if name == "rename_article":
        return _rename_articles(root, args or {}, progress)
    if name == "save_article":
        path = _inside(root, args.get("filename") or args.get("name") or args.get("path"))
        content = args.get("content")
        if content is None:
            content = args.get("text") or args.get("contents") or ""
        content = str(content)
        # 剥离自定义 GGUF 模型偶发混入 content 的 <think>...</think> 块
        from utils.ollama_agent import strip_think_blocks, pick_save_text
        content = strip_think_blocks(content)
        from_reply = False
        if content.strip() in _SAME_AS_REPLY or not content.strip():
            picked = pick_save_text(args, reply_body)
            if picked.strip():
                content = picked
                from_reply = True
            else:
                # 绝不能把口头短句写进文件（会把 6000 字的章节覆盖成十几字）。
                fn = str(args.get("filename") or args.get("name") or args.get("path") or "").strip()
                log.info(
                    "ollama article save refused empty filename=%s reply_chars=%d has_url=%s",
                    fn, len((reply_body or "").strip()), bool(str(args.get("url") or "").strip()),
                )
                if str(args.get("url") or "").strip():
                    return (
                        "没有保存：没有可用的网页正文，这一轮回复也太短，不能当文章。"
                        "请先 web_fetch 打开要保存的那一章，再 save_article（只写 filename，可选 url）。"
                    )
                return (
                    "没有保存：没有传入正文，这一轮回复也太短，不能当文章。"
                    "请把该文件的完整正文放进 save_article 的 content 参数后再保存；"
                    "或先在回复里写出该文件全文，再调用 save_article（只写 filename）。"
                    "不要在没有正文时重复调用保存。"
                )
        return _save_article(
            root, path, content, should_stop, budget,
            mode=_mode(args), reply_body=reply_body, notices=notices, from_reply=from_reply,
        )
    return f"没有这个工具：{name}"


def _resolve_open_rel(root: str, rel: str, listed=None) -> tuple:
    """把用户给的相对路径解析成可打开的绝对路径。返回 (abs_path, rel, error_or_note)。"""
    from utils.ollama_progress import _strip_path, _unique_listed_match
    raw = _strip_path(rel)
    if not raw:
        return "", "", "请提供 filename（完整相对路径，可含子目录，不要拆开）或 index。"
    hit = _unique_listed_match(raw, listed or [])
    if hit:
        raw = hit
    path = ""
    err = ""
    try:
        path = _inside(root, raw)
    except ValueError as e:
        err = str(e)
        path = ""
    if path and (os.path.isfile(path) or os.path.isdir(path)):
        return path, _rel_posix(root, path), ""
    # 精确路径不存在：按文件名在工作目录里唯一匹配
    base = os.path.basename(raw.replace("\\", "/"))
    ext = os.path.splitext(base)[1]
    if ext == "" and raw:
        # 可能是文件夹名
        hits_dir = []
        try:
            rows = iter_dir_rows(root, "", True)
            hits_dir = [r for k, r, _s in rows if k == "dir" and (r == raw or r.endswith("/" + raw) or r.rsplit("/", 1)[-1].casefold() == raw.casefold())]
        except OSError:
            hits_dir = []
        if len(hits_dir) == 1:
            try:
                d = _inside(root, hits_dir[0])
                return d, hits_dir[0], ""
            except ValueError:
                pass
    hits = find_unique_basename(root, base if ext else raw)
    if len(hits) == 1:
        try:
            path = _inside(root, hits[0])
            return path, hits[0], f"按文件名在工作目录中匹配到：{hits[0]}"
        except ValueError as e:
            err = str(e)
    if len(hits) > 1:
        lines = [f"找不到唯一的「{base}」，工作目录中有这些同名文件，请用完整相对路径或 index："]
        for i, h in enumerate(hits[:20], 1):
            lines.append(f'  #{i}  path="{h}"')
        return "", "", "\n".join(lines)
    # 附上父目录或根目录清单，避免模型反复改路径格式
    parent = raw.replace("\\", "/").rsplit("/", 1)[0] if "/" in raw.replace("\\", "/") else ""
    extra = err or f"找不到这篇文章：{raw}"
    try:
        sub = parent if parent and os.path.isdir(os.path.join(root, parent.replace("/", os.sep))) else ""
        rows = iter_dir_rows(root, sub, True)
        listing = format_listing(
            root, rows, subpath=sub, recursive=True,
            extra="请复制下面的 path 或改用 index，不要再试另一种路径写法。",
        )
        return "", "", extra + "\n" + listing
    except Exception:
        return "", "", extra


def _read_served(root, args, budget: DirBudget, read_chars: int, progress) -> str:
    """有进度时：按点名的文件读（一次一个就只读一个）；已读未改动的重复循环改回摘要。"""
    from utils.ollama_progress import resolve_read_args
    listed = []
    if progress is not None:
        # 每次读取先清掉上一次的「已供给文件」，失败时不会把上一章记成这次读到的
        progress.last_served = []
        listed = list((progress.data or {}).get("list_index") or (progress.data or {}).get("listed") or [])
    if not listed:
        listed = list_index_names(root, "", True)
    names, err = resolve_read_args(args or {}, listed)
    if err:
        return err
    if not names:
        rel = (args or {}).get("filename") or (args or {}).get("name") or (args or {}).get("path")
        if rel:
            names = [rel]
        elif progress is None:
            # 无进度时无法推断“下一篇”；有进度时交给 gate_read 取下一个未处理文件
            return "请提供 filename（完整相对路径，可含子目录，不要拆开）或 index（list_articles 的编号）。"
    start = _read_start(args)

    def _open_one(rel, start_at):
        path, resolved, note = _resolve_open_rel(root, rel, listed)
        if not path:
            return note or f"找不到这篇文章：{rel}"
        text = _read_article(path, read_chars, start_at, root=root)
        if note and not text.lstrip().startswith("工作目录：") and not text.lstrip().startswith("文件夹："):
            text = note + "\n" + text
        return text

    if progress is None:
        return _meter_read(budget, lambda: _open_one(names[0], start))

    plan = progress.gate_read(args)
    action = plan.get("action")
    if action in ("digest", "refuse"):
        return plan.get("message") or "【已有记录】请根据已有进度继续。"
    files = list(plan.get("files") or [])
    prefix = plan.get("message") or ""
    progress.last_served = list(files)
    if not files:
        return prefix or "【已有记录】没有要读的文件。"
    parts = []
    for rel in files:
        s = start if len(files) == 1 else progress.next_start(rel)
        text = _meter_read(budget, lambda r=rel, st=s: _open_one(r, st))
        if len(files) == 1:
            parts.append(_with_read_header(rel, text, listed))
        else:
            parts.append("----- %s -----\n%s" % (rel, text))
    body = "\n\n".join(parts)
    if prefix.strip() and body.strip():
        # 「已读过」提示放在正文后面：结果开头是真正读到的内容，记录也能照常记下。
        return (body.rstrip() + "\n\n" + prefix.strip()).lstrip()
    return (prefix + body).lstrip()


def _mode(args: dict) -> str:
    raw = str(args.get("mode") or "").strip().lower()
    if raw in ("append", "a", "add", "追加", "续写"):
        return "append"
    flag = args.get("append")
    if isinstance(flag, str):
        flag = flag.strip().lower() in ("1", "true", "yes", "on")
    return "append" if flag else "overwrite"


def _read_start(args: dict) -> int:
    raw = (args or {}).get("start")
    if raw is None:
        raw = (args or {}).get("offset")
    try:
        n = int(raw or 0)
    except (TypeError, ValueError):
        n = 0
    return max(0, n)


def _meter_read(budget: DirBudget, fn) -> str:
    budget.reads += 1
    return fn()


def _stopped(should_stop) -> bool:
    return bool(should_stop and should_stop())


def _root(article_dir: str) -> str:
    raw = str(article_dir or "").strip().strip('"')
    if not raw:
        raise ValueError("还没有指定保存目录。请让用户在页面上选择文件夹。")
    if not (_ABS_RE.match(raw) or os.path.isabs(raw)):
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
    try:
        from utils.ollama_progress import _strip_path
        raw = _strip_path(filename)
    except Exception:
        raw = str(filename or "").strip().strip('"').strip("'")
        raw = raw.replace("\\", "/")
    raw = raw.replace("/", os.sep).replace("\\", os.sep)
    if not raw or os.path.isabs(raw) or raw.startswith("\\\\") or _ABS_RE.match(raw):
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
    dest_as_is = os.path.abspath(os.path.join(root, *clean))
    if os.path.isdir(dest_as_is):
        if not _contained(root, dest_as_is):
            raise ValueError("文件不能放到指定目录之外。")
        return dest_as_is
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


_LIST_DEPTH = 8


def _rel_posix(root: str, path: str) -> str:
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root))
    if rel in (".", ""):
        return ""
    return rel.replace("\\", "/")


def iter_dir_rows(root: str, subpath: str = "", recursive: bool = True, cap: int = 0) -> list:
    """目录项：(kind, rel, size)。rel 是相对工作目录的路径（正斜杠）。文件夹在前，再按路径排序。"""
    cap = int(cap or _LIST_CAP)
    start = root
    prefix = ""
    subpath = (subpath or "").replace("\\", "/").strip().strip("/")
    if subpath:
        try:
            start = _inside(root, subpath)
        except ValueError:
            start = os.path.abspath(os.path.join(root, *subpath.split("/")))
        if not _contained(root, start):
            raise ValueError("只能查看工作目录内的子文件夹。")
        if not os.path.isdir(start):
            raise ValueError(f"这不是文件夹：{subpath}")
        prefix = _rel_posix(root, start)

    rows = []
    skipped = 0

    def add(dirpath: str, rel_dir: str, depth: int, recurse: bool) -> None:
        nonlocal skipped
        try:
            names = os.listdir(dirpath)
        except PermissionError:
            raise
        except OSError:
            return
        for name in names:
            if len(rows) >= cap:
                return
            child = os.path.join(dirpath, name)
            if not _contained(root, child):
                continue
            rel = f"{rel_dir}/{name}" if rel_dir else name
            rel = rel.replace("\\", "/")
            if os.path.isdir(child):
                rows.append(("dir", rel, 0))
                if recurse and depth < _LIST_DEPTH:
                    add(child, rel, depth + 1, True)
            elif os.path.isfile(child):
                ext = os.path.splitext(name)[1].lower()
                if ext not in _TEXT_EXT and rel_dir and recurse:
                    skipped += 1
                    continue
                if ext not in _TEXT_EXT and ext != "" and recurse:
                    skipped += 1
                    continue
                try:
                    size = os.path.getsize(child)
                except OSError:
                    size = 0
                rows.append(("file", rel, size))

    add(start, prefix, 0, recursive)
    rows.sort(key=lambda r: (0 if r[0] == "dir" else 1, natural_key(r[1])))
    return rows


def iter_text_file_rels(root: str) -> list:
    """工作目录内全部文本文件的相对路径（含子目录）。"""
    try:
        rows = iter_dir_rows(root, "", True, _LIST_CAP)
    except OSError:
        return []
    return [rel for kind, rel, _sz in rows if kind == "file"]


def list_index_names(root: str, subpath: str = "", recursive: bool = True) -> list:
    try:
        return [rel for _kind, rel, _size in iter_dir_rows(root, subpath, recursive)]
    except OSError:
        return []


def _truthy(val) -> bool:
    if val is True:
        return True
    if val is False or val is None:
        return False
    s = str(val).strip().lower()
    return s in ("1", "true", "yes", "on", "all", "recursive", "全部", "递归", "是")


def _list_target(root: str, args: dict, progress=None) -> tuple:
    """解析 list_articles 的 path/index。返回 (subpath, error)。"""
    args = args or {}
    from utils.ollama_progress import parse_index, _strip_path
    listed = []
    if progress is not None:
        listed = list((progress.data or {}).get("list_index") or [])
    if not listed:
        listed = list_index_names(root, "", True)
    path, err = parse_index(args, listed)
    if err:
        return "", err
    if not path:
        path = _strip_path(args.get("path") or args.get("filename") or args.get("name") or args.get("folder") or "")
        path = as_rel_in_root(root, path)
    if not path:
        return "", ""
    # 若 index/path 指向文件，列出其所在目录
    try:
        dest = _inside(root, path)
    except ValueError as e:
        return "", str(e)
    if os.path.isfile(dest):
        parent = _rel_posix(root, os.path.dirname(dest))
        return parent, ""
    if os.path.isdir(dest):
        return _rel_posix(root, dest), ""
    return path, ""


def format_listing(root: str, rows: list, *, subpath: str = "", recursive: bool = True, extra: str = "") -> str:
    title = f"工作目录：{root}" if not subpath else f"文件夹：{subpath}（位于 {root}）"
    hint = (
        "读取时请用 index=编号，或把 path 引号内的完整相对路径原样复制到 filename"
        "（可含斜杠、空格、逗号等，不要拆开）。"
        "查看某个子文件夹可用 list_articles path=\"子目录\"；递归列出用 recursive=true。"
    )
    lines = [title, f"共 {len(rows)} 项。{hint}"]
    if extra:
        lines.append(extra)
    lines.append("")
    shown = rows[:_LIST_CAP]
    for i, (kind, rel, size) in enumerate(shown, 1):
        label = "[文件夹]" if kind == "dir" else "[文件]"
        size_s = "" if kind == "dir" else f"  {_fmt_size(size)}"
        q = rel.replace("\\", "/").replace('"', '\\"')
        lines.append(f'#{i}  {label}  path="{q}"{size_s}')
    if len(rows) > _LIST_CAP:
        lines.append(f"还有 {len(rows) - _LIST_CAP} 项没有列出。")
    return "\n".join(lines)


def _list_articles(root: str, args: dict = None, progress=None) -> str:
    args = args or {}
    recursive_arg = args.get("recursive") if args.get("recursive") is not None else args.get("all")
    # 默认递归，以便 skill 子目录里的 SKILL.md、references/*.md 能出现在清单中
    if recursive_arg is None:
        recursive = True
    else:
        recursive = _truthy(recursive_arg)
    try:
        subpath, err = _list_target(root, args, progress)
        if err:
            return err
        rows = iter_dir_rows(root, subpath, recursive)
    except PermissionError:
        return f"没有权限查看：{root}"
    except ValueError as e:
        return str(e)
    except OSError as e:
        return f"没有看成：{root}（{e}）"
    text = format_listing(root, rows, subpath=subpath, recursive=recursive)
    rels = [rel for _k, rel, _s in rows]
    if progress is not None:
        try:
            progress.data["list_index"] = rels
            files = [rel for kind, rel, _s in rows if kind == "file"]
            listed = list(progress.data.get("listed") or [])
            known = set(listed)
            for rel in files:
                if rel not in known:
                    listed.append(rel)
                    known.add(rel)
            progress.data["listed"] = listed
        except Exception:
            pass
    return text


def find_unique_basename(root: str, name: str) -> list:
    """在工作目录（含子目录）中按文件名查找，返回相对路径列表。"""
    base = os.path.basename((name or "").replace("\\", "/").strip())
    if not base:
        return []
    hits = []
    needle = base.casefold()
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            if not _contained(root, dirpath):
                dirnames[:] = []
                continue
            for fn in filenames:
                if fn.casefold() != needle:
                    continue
                full = os.path.join(dirpath, fn)
                if _contained(root, full) and os.path.isfile(full):
                    hits.append(_rel_posix(root, full))
            if len(hits) > 20:
                break
    except OSError:
        return hits
    return hits


_CONVERT_SRC = {".md", ".markdown"}


def _read_whole(path: str) -> str:
    with open(path, "rb") as f:
        data = f.read()
    if b"\x00" in data[:4096]:
        raise ValueError("不是文本")
    return _decode(data, False).replace("\r\n", "\n").replace("\r", "\n")


def _named_md(root: str, text: str) -> list:
    """句子里写出了完整文件名或《标题》时只转这些；否则转目录里全部 markdown。

    不拿标题去对「文件」「目录」这类指令用词，否则「把工作目录中的文件转为 txt」
    会误当成只转某一个文件。
    """
    said = text or ""
    folded = said.lower()
    rows = []
    named = []
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if not os.path.isfile(path) or not _contained(root, path):
            continue
        stem, ext = os.path.splitext(name)
        if ext.lower() not in _CONVERT_SRC:
            continue
        rows.append(path)
        if name.lower() in folded or (stem and f"《{stem}》" in said):
            named.append(path)
    return named or rows


def export_txt(article_dir: str, text: str = "") -> str:
    """工作目录里的 .md 原样写成同名 .txt。不删原文，不改内容，不调用模型。"""
    root = _root(article_dir)
    from utils.ollama_auto import unique_path, write_text

    sources = _named_md(root, text)
    if not sources:
        return "工作目录里没有需要转成 txt 的 .md 文件。"
    wrote, same, failed = [], [], []
    for src in sources:
        stem = os.path.splitext(os.path.basename(src))[0]
        try:
            body = _read_whole(src)
        except Exception as e:
            failed.append(f"{os.path.basename(src)}（{e}）")
            continue
        if body and not body.endswith("\n"):
            body += "\n"
        plain = os.path.join(root, stem + ".txt")
        if os.path.isfile(plain):
            try:
                if _read_whole(plain) == body:
                    same.append(os.path.basename(plain))
                    continue
            except Exception:
                pass
            dest = unique_path(root, stem, ".txt")
        else:
            dest = plain
        n = write_text(dest, body)
        wrote.append(f"{os.path.basename(src)} → {os.path.basename(dest)}（{n} 字）")
        log.info("ollama 转为txt %s → %s 字数=%d", src, dest, n)
    lines = []
    if wrote:
        lines.append("已按原文转成 txt，原来的文件还在：")
        lines.extend(wrote)
    if same:
        lines.append("这些 txt 已有相同内容，没有再写一份：" + "、".join(same))
    if failed:
        lines.append("没有转成：" + "、".join(failed))
    return "\n".join(lines) if lines else "没有文件被转成 txt。"


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


def _read_article(path: str, read_chars: int = 0, start: int = 0, root: str = "") -> str:
    if os.path.isdir(path):
        try:
            sub = _rel_posix(root, path) if root else os.path.basename(path)
            rows = iter_dir_rows(root or os.path.dirname(path), sub, True)
            return format_listing(
                root or os.path.dirname(path), rows, subpath=sub, recursive=True,
                extra="这是文件夹，不是文章。请用下面的 path 或 index 读取其中的文件，不要反复改路径格式。",
            )
        except Exception as e:
            return f"这是文件夹，请改读其中的文章文件。（{e}）"
    if not os.path.isfile(path):
        return f"找不到这篇文章：{os.path.basename(path)}"
    ext = os.path.splitext(path)[1].lower()
    if ext not in _TEXT_EXT:
        return "只能阅读 .md 或 .txt 文章。"
    start = max(0, int(start or 0))
    read_chars = int(read_chars or 0)
    # 指定 start 时要读到该起点之后，字节上限按起点向后再留一段。
    byte_limit = _READ_CAP
    if start:
        byte_limit = max(_READ_CAP, (start + max(read_chars, 8000) + 1024) * 4)
    try:
        with open(path, "rb") as f:
            data = f.read(byte_limit + 1)
    except PermissionError:
        return f"没有权限读取：{os.path.basename(path)}"
    except OSError as e:
        return f"没有读成：{os.path.basename(path)}（{e}）"
    if b"\x00" in data[:4096]:
        return "这不是文本文章，没有读取。"
    file_cut = len(data) > byte_limit
    data = data[:byte_limit]
    text = _decode(data, file_cut)
    # start 落在已解码范围之外：文件比这次读到的更长，提示继续。
    if start >= len(text):
        if file_cut:
            return (
                f"还没有读到第 {start} 字附近（本次从文件开头解开 {len(text)} 字）。"
                f"请再次调用 read_article，filename 不变，start={max(0, len(text) - 100)}。"
            )
        return f"已经读到末尾（全文 {len(text)} 字）。"
    chunk = text[start:]
    truncated = file_cut or (read_chars > 0 and len(chunk) > read_chars)
    if read_chars > 0 and len(chunk) > read_chars:
        chunk = chunk[:read_chars]
        truncated = True
    got = len(chunk)
    next_start = start + got
    remain = None
    if not file_cut:
        remain = len(text) - next_start
    elif truncated:
        remain = -1
    key = _key(path)
    if truncated or remain not in (0, None):
        _PARTIAL_READS[key] = next_start
        if remain is None or remain < 0:
            extra = (
                f"\n…（全文更长，本次从第 {start} 字读到第 {next_start} 字。"
                f"请再次调用 read_article，filename 不变，并加上 start={next_start} 继续读完。"
                "不要整篇覆盖这个文件，否则没读到的部分会丢失。）"
            )
        else:
            extra = (
                f"\n…（全文 {len(text)} 字，本次从第 {start} 字读到第 {next_start} 字，后面还有 {remain} 字。"
                f"请再次调用 read_article，filename 不变，并加上 start={next_start} 继续读完。"
                "不要整篇覆盖这个文件，否则没读到的部分会丢失。）"
            )
        chunk += extra
        log.info("ollama article partial read %s start=%d chars=%d", path, start, got)
    elif start == 0:
        _PARTIAL_READS.pop(key, None)
    return chunk


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


_RENAME_REFUSE = (
    "这是整理文件名的任务，按用户要求不要读取或改写文件内容。"
    "请调用 rename_article：filename 或 index 指定原文件，new_filename 可省略"
    "（省略时程序只保留原名中的汉字和数字，并保留扩展名）。"
    "也可传入 renames 列表，或 mode=han_digits 按同一规则批量处理清单中的文件。"
    "目标名若已存在，程序不会覆盖已有文件。"
)
_FW_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def _is_rename_only_progress(progress) -> bool:
    if progress is None:
        return False
    try:
        from utils.ollama_intent import is_rename_only_task
        card = (progress.data or {}).get("task_card") or {}
        text = card.get("user_input") or (progress.data or {}).get("goal") or ""
        if text:
            return bool(is_rename_only_task(text) or card.get("rename_only"))
        return bool(card.get("rename_only") or getattr(progress, "data", {}).get("rename_only"))
    except Exception:
        return False


def han_digit_stem(stem: str) -> str:
    """只保留汉字和数字（全角数字会先换成半角）。"""
    text = str(stem or "").translate(_FW_DIGITS)
    return "".join(
        ch for ch in text
        if ("\u4e00" <= ch <= "\u9fff") or ch.isdigit()
    )


def _listing_for(root: str, progress) -> list:
    if progress is not None:
        order = list((progress.data or {}).get("list_index") or (progress.data or {}).get("listed") or [])
        if order:
            return order
    return list_index_names(root)


def _src_from_item(root: str, item: dict, listed: list) -> tuple:
    """返回 (src_abs, rel, error)。"""
    from utils.ollama_progress import resolve_read_args, _strip_path, rel_path
    item = item or {}
    names, err = resolve_read_args(item, listed)
    if err:
        return "", "", err
    rel = names[0] if names else _strip_path(item.get("filename") or item.get("name") or item.get("path") or "")
    if not rel:
        return "", "", "请提供 filename 或 index（对应 list_articles 的编号）。"
    path = os.path.join(root, rel.replace("/", os.sep))
    if not os.path.isfile(path):
        try:
            path = _inside(root, rel)
        except ValueError as e:
            return "", rel, str(e)
    if not os.path.isfile(path):
        return "", rel, f"找不到这篇文章：{os.path.basename(path)}"
    try:
        rel = rel_path(root, path)
    except Exception:
        rel = os.path.basename(path)
    return path, rel, ""


def _dest_from_src(root: str, src_path: str, new_filename: str, auto: bool) -> tuple:
    """返回 (dest_abs, dest_rel, note, error)。auto=True 时冲突则加唯一后缀；显式新名冲突则拒绝。"""
    from utils.ollama_progress import _strip_path, rel_path
    src_ext = os.path.splitext(src_path)[1]
    raw = _strip_path(new_filename)
    if not raw:
        return "", "", "", "新文件名为空，没有改名。"
    # 未带扩展名则沿用原扩展名
    base = raw.replace("\\", "/")
    stem, ext = os.path.splitext(os.path.basename(base))
    if not ext:
        ext = src_ext
        if "/" in base.rstrip("/"):
            base = base.rstrip("/") + ext
        else:
            base = stem + ext
    # 相对路径：若只是新文件名，放到与源文件同一目录
    if "/" not in base and "\\" not in new_filename:
        parent = os.path.dirname(src_path)
        dest = os.path.join(parent, os.path.basename(base))
    else:
        try:
            dest = _inside(root, base)
        except ValueError as e:
            return "", "", "", str(e)
    dest = os.path.abspath(dest)
    if not _contained(root, dest):
        return "", "", "", "文件不能放到指定目录之外。"
    src_real = os.path.normcase(os.path.abspath(src_path))
    dest_real = os.path.normcase(dest)
    try:
        rel = rel_path(root, dest)
    except Exception:
        rel = os.path.basename(dest)
    if src_real == dest_real:
        return dest, rel, "已是该名称，无需改名。", ""
    if os.path.exists(dest):
        if not auto:
            return "", rel, "", f"目标已存在，未改名以免覆盖：{rel}"
        from utils.ollama_auto import unique_path
        stem2, ext2 = os.path.splitext(os.path.basename(dest))
        dest = unique_path(os.path.dirname(dest), stem2, ext2)
        try:
            rel = rel_path(root, dest)
        except Exception:
            rel = os.path.basename(dest)
        return dest, rel, f"目标已有同名文件，已改为 {rel} 以免覆盖。", ""
    return dest, rel, "", ""


def _rename_one(root: str, item: dict, listed: list, default_han: bool) -> dict:
    """执行一次重命名，不读、不改写文件内容。"""
    src, rel, err = _src_from_item(root, item, listed)
    if err:
        return {"ok": False, "old": rel, "new": "", "message": err}
    new_name = str(item.get("new_filename") or item.get("new_name") or item.get("to") or "").strip()
    auto = False
    if not new_name:
        if not default_han:
            return {"ok": False, "old": rel, "new": "", "message": "请提供 new_filename，或使用 mode=han_digits 由程序按「只留汉字和数字」生成新名。"}
        stem, ext = os.path.splitext(os.path.basename(src))
        stem2 = han_digit_stem(stem)
        if not stem2:
            return {"ok": False, "old": rel, "new": "", "message": f"原名去掉符号后没有汉字或数字，未改名：{rel}"}
        new_name = stem2 + ext
        auto = True
    else:
        auto = False
    dest, dest_rel, note, err = _dest_from_src(root, src, new_name, auto=auto)
    if err:
        return {"ok": False, "old": rel, "new": dest_rel, "message": err}
    if note.startswith("已是该名称"):
        return {"ok": True, "old": rel, "new": dest_rel, "message": note, "skipped": True}
    try:
        os.rename(src, dest)
    except OSError as e:
        return {"ok": False, "old": rel, "new": dest_rel, "message": f"没有改名：{rel}（{e}）"}
    msg = f"已重命名：{rel} → {dest_rel}"
    if note:
        msg += "。" + note
    return {"ok": True, "old": rel, "new": dest_rel, "message": msg}


def _iter_rename_items(args: dict, listed: list) -> tuple:
    """取出待改名条目。返回 (items, default_han, error)。"""
    args = args or {}
    mode = str(args.get("mode") or args.get("rule") or "").strip().lower()
    default_han = mode in ("han_digits", "han-digit", "han", "digits", "只留汉字和数字", "汉字数字")
    raw = args.get("renames") if args.get("renames") is not None else args.get("items")
    items = []
    if isinstance(raw, list):
        for it in raw:
            if isinstance(it, dict):
                items.append(it)
            elif isinstance(it, str) and it.strip():
                items.append({"filename": it.strip(), "new_filename": ""})
    if not items:
        # 单条：filename/index + 可选 new_filename
        has = any(str(args.get(k) or "").strip() for k in ("filename", "name", "path", "index", "id", "n"))
        if has:
            items.append(dict(args))
    if not items and default_han:
        # 批量：清单里的文件
        for name in listed:
            if name.endswith(("/", "\\")):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in _TEXT_EXT:
                items.append({"filename": name})
        if not items:
            return [], default_han, "清单里没有可重命名的文件。请先调用 list_articles。"
    if not items:
        return [], default_han, "请提供 filename 或 index，或传入 renames 列表，或 mode=han_digits 批量处理。"
    return items, default_han, ""


def _rename_articles(root: str, args: dict, progress=None) -> str:
    listed = _listing_for(root, progress)
    items, default_han, err = _iter_rename_items(args, listed)
    if err:
        return err
    results = []
    for item in items:
        rec = _rename_one(root, item, listed, default_han)
        results.append(rec)
        if rec.get("ok") and rec.get("new") and rec.get("old") and rec["new"] != rec["old"]:
            # 后续条目用更新后的清单解析编号
            listed = [(rec["new"] if x == rec["old"] else x) for x in listed]
            if rec["new"] not in listed:
                listed.append(rec["new"])
    ok = [r for r in results if r.get("ok") and not r.get("skipped")]
    skipped = [r for r in results if r.get("skipped")]
    bad = [r for r in results if not r.get("ok")]
    lines = []
    if ok:
        lines.append(f"已重命名 {len(ok)} 个文件（只改文件名，未改内容）：")
        for r in ok:
            extra = r["message"].split("。", 1)[-1] if "以免覆盖" in (r.get("message") or "") else ""
            line = "  " + r["old"] + " → " + r["new"]
            if extra:
                line += "；" + extra
            lines.append(line)
    if skipped:
        lines.append("这些文件已是目标名称，无需改名：" + "、".join(r["old"] for r in skipped))
    if bad:
        lines.append(f"没有改名 {len(bad)} 个：")
        for r in bad:
            lines.append("  " + (r.get("message") or r.get("old") or "失败"))
    if not lines:
        return "没有文件被重命名。"
    return "\n".join(lines)
