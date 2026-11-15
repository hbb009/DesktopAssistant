# utils/ollama_progress.py
# 程序自己记下 Agent 每一步的进度。哪一步超出上下文或批次，就从这份记录接着做，
# 不靠次数上限截停任务，也不让模型或用户去判断额度。
# 升级优化：
#   1. 新任务首轮自动附上工作目录文件清单（带 #index 与 path），省去模型首轮盲猜或空跑 list_articles；
#   2. 取消强制 save_notes 硬门禁（不再拒绝 read_article），由程序在后台自动提取文件摘要记账；
#   3. 放宽笔记最小字数限制，避免模型因笔记字数不足报错死循环。

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from typing import Callable, Optional

from utils.logger import get_logger

log = get_logger("deskassist.ollama")

SCHEMA = 3
TEXT_EXT = {".md", ".txt", ".markdown"}
BATCH_FILES = 5
BATCH_CHARS = 24000
HASH_FULL_MAX = 8 * 1024 * 1024
MIN_NOTES = 10
NOTES_GATE_CHARS = 40000
BATCH_MARK = "【批次文件："

_FULL_READ_HINTS = (
    "整理", "skill", "Skill", "SKILL", "全部", "每一章", "所有章",
    "读完", "分析", "风格", "全书", "小说", "分批", "写成", "改写成",
)

_READ_FAIL = (
    "这是文件夹",
    "找不到这篇文章",
    "只能阅读",
    "没有权限读取",
    "没有读成",
    "这不是文本文章",
    "只能填写文件名",
    "文件名不能",
    "文件名不合法",
    "文件名是空的",
    "文件不能放到",
    "还没有指定",
    "文章目录没有开启",
    "已停止",
    "没有这个工具",
    "这是整理文件名的任务",
    "请调用 rename_article",
    "【已有记录】",
    "【不必再读】",
    "【请先写笔记】",
)

_LIST_FAIL = (
    "只能填写文件名",
    "这不是文件夹",
    "只能查看",
    "文件名不",
    "文件不能放到",
    "没有这个编号",
    "还没有目录清单",
    "index 必须是",
    "没有权限查看",
    "没有看成",
    "还没有指定",
    "保存目录",
    "这个位置",
    "文章目录没有开启",
    "已停止",
)

_NEXT_TOKENS = {
    "", "下一批", "下一批未读", "next", "unread", "next_batch", "*",
    "未读", "未读批次",
}

_NOTES_NAME_RE = re.compile(
    r"(notes?|analysis|style-guide|evaluation|SKILL\.md|references[/\\])",
    re.I,
)

_READ_TO_RE = re.compile(r"本次从第 (\d+) 字读到第 (\d+) 字")
_SAVED_RE = re.compile(r"^已保存：(.+?)（")
_SAVED_CHARS_RE = re.compile(r"（(?:追加 )?(\d+) 字")
_SPLIT_NAMES = re.compile(r"[\n]+")
_INDEX_RE = re.compile(r"^#\s*(\d+)$")
_ASCII_LIST_SPLIT = re.compile(r"\s*,\s*")
_TEXT_NAME_RE = re.compile(r"\.(md|txt|markdown)$", re.I)
_QUOTE_PAIRS = (("\"", "\""), ("'", "'"), ("“", "”"), ("‘", "’"), ("「", "」"), ("『", "』"))


def default_store() -> str:
    from utils.app_paths import userdata_dir
    d = os.path.join(userdata_dir(), "agent-progress")
    os.makedirs(d, exist_ok=True)
    return d


def task_key(article_dir: str) -> str:
    norm = os.path.normcase(os.path.abspath(article_dir or ""))
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def _safe_session_id(session_id: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_\-]", "", str(session_id or ""))[:64]
    return s or "session"


def progress_path(article_dir: str, store_dir: Optional[str] = None, session_id: Optional[str] = None) -> str:
    store = store_dir or default_store()
    key = task_key(article_dir)
    if session_id:
        return os.path.join(store, key, _safe_session_id(session_id) + ".json")
    return os.path.join(store, key + ".json")


def workspace_legacy_path(article_dir: str, store_dir: Optional[str] = None) -> str:
    return os.path.join(store_dir or default_store(), task_key(article_dir) + ".json")


def list_prior_record_paths(article_dir: str, store_dir: Optional[str] = None, exclude: str = "") -> list:
    """同一工作目录下、当前会话之外的历史记录，新的在前。不删用户成果文件。"""
    store = store_dir or default_store()
    key = task_key(article_dir)
    paths = []
    legacy = os.path.join(store, key + ".json")
    if os.path.isfile(legacy):
        paths.append(legacy)
    folder = os.path.join(store, key)
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            if name.endswith(".json"):
                paths.append(os.path.join(folder, name))
    ex = os.path.normcase(os.path.abspath(exclude)) if exclude else ""
    out = []
    seen = set()
    for p in paths:
        ap = os.path.normcase(os.path.abspath(p))
        if ex and ap == ex:
            continue
        if ap in seen:
            continue
        seen.add(ap)
        out.append(p)

    def _mtime(path: str) -> float:
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    def _weight(path: str) -> tuple:
        data = load_progress(path)
        filled = bool((data.get("written") or data.get("read") or data.get("notes") or data.get("steps")))
        return (1 if filled else 0, _mtime(path))

    out.sort(key=_weight, reverse=True)
    return out


FRESH_TAG = "【本次任务，由程序锁定】"


def load_latest_prior(article_dir: str, store_dir: Optional[str] = None, exclude: str = "") -> tuple:
    for path in list_prior_record_paths(article_dir, store_dir, exclude=exclude):
        data = load_progress(path)
        if not data:
            continue
        if os.path.normcase(data.get("article_dir") or "") != os.path.normcase(os.path.abspath(article_dir or "")):
            if data.get("article_dir"):
                continue
        if data.get("written") or data.get("read") or data.get("notes") or data.get("steps") or data.get("task_card"):
            return data, path
    return {}, ""


def is_same_task(user_text: str, prior: dict) -> bool:
    """新消息与旧任务卡是否明显是同一份工作（同一批输出文件或同一段目标）。"""
    prior = prior or {}
    text = (user_text or "").strip()
    if not text or not prior:
        return False
    card = prior.get("task_card") or {}
    old_goal = _one_line(card.get("goal") or prior.get("goal") or "")
    new_card = extract_task_card(text, listed=prior.get("listed") or [], article_dir=prior.get("article_dir") or "")
    new_goal = _one_line(new_card.get("goal") or text)
    old_out = {str(x).replace("\\", "/") for x in (card.get("outputs") or []) if x}
    new_out = {str(x).replace("\\", "/") for x in (new_card.get("outputs") or []) if x}
    if old_out and new_out and old_out == new_out:
        return True
    if old_out and new_out and (old_out & new_out):
        old_dirs = {o.rsplit("/", 1)[0] for o in old_out if "/" in o}
        new_dirs = {o.rsplit("/", 1)[0] for o in new_out if "/" in o}
        if old_dirs and old_dirs == new_dirs:
            return True
    if len(old_goal) >= 24 and len(new_goal) >= 24:
        if old_goal == new_goal or old_goal in new_goal or new_goal in old_goal:
            return True
    return False


def should_resume_prior(user_text: str, prior: dict) -> bool:
    """仅在用户明确说「继续 / 接着做 / 接着上次」等时续做。重发同一条命令不算续做。"""
    if not prior:
        return False
    return _is_continue_message(user_text)


def rel_path(root: str, path: str) -> str:
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root))
    return rel.replace("\\", "/")


def file_stamp(path: str) -> dict:
    try:
        st = os.stat(path)
    except OSError:
        return {}
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            if st.st_size <= HASH_FULL_MAX:
                digest.update(f.read())
            else:
                digest.update(f.read(65536))
                digest.update(b"#")
                digest.update(str(st.st_size).encode("ascii"))
    except OSError:
        return {"size": st.st_size, "mtime": int(st.st_mtime)}
    return {
        "size": st.st_size,
        "mtime": int(st.st_mtime),
        "sha256": digest.hexdigest()[:16],
    }


def atomic_save(path: str, data: dict) -> None:
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".progress-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def load_progress(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def new_record(article_dir: str, goal: str = "") -> dict:
    return {
        "schema": SCHEMA,
        "article_dir": os.path.abspath(article_dir or ""),
        "goal": _one_line(goal),
        "updated": "",
        "listed": [],
        "read": {},
        "written": {},
        "pending": [],
        "notes": [],
        "awaiting_notes": [],
        "steps": [],
        "task_card": {},
        "stage": "",
        "trace": [],
        "session_id": "",
    }


def _name_key(name: str):
    from utils.ollama_dirs import natural_key
    return natural_key(name)


def strip_read_header(text: str) -> str:
    """去掉读取结果开头的程序标注行（文件名、按文件名匹配），只留正文。"""
    s = (text or "").lstrip()
    while s.startswith(("【本次读取的文件：", "按文件名在工作目录中匹配到：")):
        s = s.split("\n", 1)[1].lstrip() if "\n" in s else ""
    return s


def _one_line(text: str) -> str:
    return " ".join((text or "").split())[:240]


_FILE_NAME_RE = re.compile(
    r"([A-Za-z0-9_\u4e00-\u9fff\-]+(?:/[A-Za-z0-9_\u4e00-\u9fff\-]+)*)\.(md|txt|markdown)",
    re.I,
)
_FILE_VERB_RE = re.compile(
    r"^(?:阅读|读取|打开|查看|参考|根据|看看|保存为|另存为|并保存为|并保存|写成|写到|写为|输出为|输出到|生成为|生成)"
)
_CONTINUE_RE = re.compile(
    r"(继续|接着做|接着来|接着上次|从上次|从进度|请从.*进度|往下做|然后继续|请继续|继续刚才|刚才的任务|上次的任务)(?!写)",
)
_WRITE_ONE_RE = re.compile(
    r"(?:写|保存|输出|生成|整理)一份\s*[「『《\"']?([^\s，。,」』》\"']{1,40})"
)
_BOUNDS = (
    "只能在工作目录内写入文件；不能写到该目录以外；"
    "不能做超出既有规则的破坏性操作（例如删除目录外的内容、整篇覆盖尚未读完的文件）。"
)


def _is_continue_message(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if len(t) <= 40 and _CONTINUE_RE.search(t) and not _FILE_NAME_RE.search(t):
        return True
    if t in ("继续", "请继续", "接着做", "接着上次", "请从上次的进度继续", "从进度接着做", "继续刚才的任务", "接着刚才的任务"):
        return True
    return False


def _norm_rel(name: str) -> str:
    return (name or "").strip().strip("「」『』《》\"'").replace("\\", "/")


def extract_task_card(user_text: str, listed=None, article_dir: str = "") -> dict:
    """从用户这句话和工作目录清单提炼任务卡，不另调模型。"""
    text = (user_text or "").strip()
    listed = [str(x).replace("\\", "/") for x in (listed or []) if x]
    inputs, outputs = [], []

    def _clean_name(raw: str) -> str:
        name = _norm_rel(raw)
        name = _FILE_VERB_RE.sub("", name, count=1)
        name = re.sub(r"^[和与及]", "", name)
        return _norm_rel(name)

    def _names_in(chunk: str) -> list:
        found = []
        for m in _FILE_NAME_RE.finditer(chunk or ""):
            name = _clean_name(m.group(0))
            if name and name not in found:
                found.append(name)
        return found

    splitter = re.search(
        r"(保存为|另存为|写成|写到|写为|保存到|输出为|输出到|生成为|并保存为)",
        text,
    )
    if splitter:
        inputs = _names_in(text[:splitter.start()])
        outputs = _names_in(text[splitter.end():])
        if not outputs:
            outputs = _names_in(text[splitter.start():])
            outputs = [n for n in outputs if n not in inputs]
    else:
        for name in _names_in(text):
            if name not in inputs:
                inputs.append(name)
    for m in _WRITE_ONE_RE.finditer(text):
        name = _norm_rel(m.group(1))
        if not name:
            continue
        if not re.search(r"\.(md|txt|markdown)$", name, re.I):
            if name in ("摘要", "说明", "结果", "报告", "大纲", "笔记", "总结", "目录"):
                name = name + ".md"
            else:
                continue
        if name not in outputs:
            outputs.append(name)
    inputs = [n for n in inputs if n not in outputs]
    if not inputs and listed:
        inputs = list(listed[:20])
    goal = _one_line(text) or "完成用户这一次提出的任务"
    input_bits = []
    if text:
        input_bits.append(_one_line(text)[:400])
    if listed:
        input_bits.append("工作目录文件：" + "、".join(listed[:20]) + (" 等" if len(listed) > 20 else ""))
    if article_dir:
        input_bits.append("工作目录：" + article_dir)
    if outputs:
        expected = "保存 " + "、".join(outputs) + "（须有正文）"
        success = "已保存 " + "、".join(outputs) + "，且每个文件都有正文。"
    else:
        expected = "完成用户要求，必要结果写入工作目录或在最终回复中说明。"
        success = "用户要求已完成，必要结果已保存或已在最终回复中说明。"
    try:
        from utils.ollama_intent import is_rename_only_task
        rename_only = is_rename_only_task(text)
    except Exception:
        rename_only = False
    if rename_only:
        expected = "只改文件名（按要求整理，例如只保留汉字和数字并保留扩展名），不读取、不改写文件内容。"
        success = "工作目录中需要整理的文件已重命名，且文件内容未被改写。"
        outputs = []
    return {
        "goal": goal,
        "input": "；".join(input_bits) if input_bits else goal,
        "expected_output": expected,
        "success": success,
        "bounds": _BOUNDS,
        "outputs": outputs,
        "inputs": inputs,
        "user_input": text,
        "requires_all_sources": bool(re.search(r"(?:读|阅读|分析|学习|研究|理解|看完).{0,20}(?:全部|所有|每一|逐章)|(?:全部|所有|每一|逐章|逐篇).{0,20}(?:章节|文章|文件|材料|内容|读)", text)) and not rename_only,
        "source_files": [n for n in listed if n not in outputs],
        "rename_only": rename_only,
    }


def format_task_card(card: dict) -> str:
    card = card or {}
    if not card:
        return ""
    lines = ["【任务卡】"]
    if card.get("goal"):
        lines.append("目标：" + card["goal"])
    if card.get("input"):
        lines.append("输入：" + card["input"])
    if card.get("expected_output"):
        lines.append("预期输出：" + card["expected_output"])
    if card.get("success"):
        lines.append("成功标准：" + card["success"])
    if card.get("bounds"):
        lines.append("边界：" + card["bounds"])
    return "\n".join(lines)


def _strip_path(raw: str) -> str:
    """去掉包裹引号和首尾空白，保留路径中间的空格与标点。"""
    s = str(raw or "").strip()
    for a, b in _QUOTE_PAIRS:
        if len(s) >= 2 and s[0] == a and s[-1] == b:
            s = s[1:-1].strip()
            break
    return s.replace("\\", "/")


def _filename_of(args: dict) -> str:
    args = args or {}
    raw = str(args.get("filename") or args.get("name") or args.get("path") or "").strip()
    return _strip_path(raw)


def _listed_norm(listed) -> list:
    out = []
    seen = set()
    for n in listed or []:
        s = _strip_path(n)
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _unique_listed_match(text: str, listed) -> str:
    text = _strip_path(text)
    if not text:
        return ""
    listed = _listed_norm(listed)
    if text in listed:
        return text
    fold = {n.casefold(): n for n in listed}
    if text.casefold() in fold:
        return fold[text.casefold()]
    hits = [n for n in listed if n.endswith("/" + text)]
    if len(hits) == 1:
        return hits[0]
    base = text.rsplit("/", 1)[-1]
    if base:
        hits = [n for n in listed if n.rsplit("/", 1)[-1].casefold() == base.casefold()]
        if len(hits) == 1:
            return hits[0]
    return ""


def _parts_from_files_string(text: str, listed) -> list:
    text = str(text or "").strip()
    if not text:
        return []
    listed = _listed_norm(listed)
    whole = _strip_path(text)
    if _unique_listed_match(whole, listed) or (listed and whole in listed):
        return [_unique_listed_match(whole, listed) or whole]
    if any(ch in whole for ch in "，、；"):
        return [whole]
    lines = [x.strip() for x in _SPLIT_NAMES.split(text) if x.strip()]
    if len(lines) > 1:
        return [_unique_listed_match(x, listed) or _strip_path(x) for x in lines]
    if "," in whole:
        parts = [_strip_path(x) for x in _ASCII_LIST_SPLIT.split(whole) if x.strip()]
        if len(parts) > 1 and all(_unique_listed_match(p, listed) or _TEXT_NAME_RE.search(p) for p in parts):
            return [_unique_listed_match(p, listed) or p for p in parts]
    return [whole]


def parse_index(args: dict, listed) -> tuple:
    args = args or {}
    raw = args.get("index")
    if raw is None or str(raw).strip() == "":
        raw = args.get("id")
    if raw is None or str(raw).strip() == "":
        raw = args.get("n")
    fn = _filename_of(args)
    if (raw is None or str(raw).strip() == "") and fn:
        m = _INDEX_RE.match(fn.strip())
        if m:
            raw = m.group(1)
    if raw is None or str(raw).strip() == "":
        return None, ""
    try:
        n = int(str(raw).strip().lstrip("#").strip())
    except (TypeError, ValueError):
        return None, "index 必须是正整数（对应 list_articles 清单里的 #编号）。"
    listed = _listed_norm(listed)
    if not listed:
        return None, "还没有目录清单。请先调用 list_articles，再使用 index 或完整 path。"
    if n < 1 or n > len(listed):
        return None, f"没有这个编号：#{n}。当前清单共 {len(listed)} 项，请用 index=1..{len(listed)} 或复制完整 path。"
    return listed[n - 1], ""


def _one_or_many(text: str, listed) -> list:
    text = _strip_path(text)
    if not text:
        return []
    listed = _listed_norm(listed)
    hit = _unique_listed_match(text, listed)
    if hit:
        return [hit]
    parts = [_strip_path(x) for x in re.split(r"[、，,;；]", text) if x.strip()]
    if len(parts) > 1 and listed and all(p in listed for p in parts):
        return parts
    return [text]


def parse_filenames(args: dict, listed=None) -> list:
    args = args or {}
    listed = _listed_norm(listed)
    names = []
    raw_list = args.get("filenames") if args.get("filenames") is not None else args.get("files")
    if isinstance(raw_list, str):
        names.extend(_parts_from_files_string(raw_list, listed))
    elif isinstance(raw_list, (list, tuple)):
        for item in raw_list:
            text = _strip_path(item)
            if text:
                names.extend(_one_or_many(text, listed))
    one = _filename_of(args)
    if one and not _INDEX_RE.match(one.strip()):
        names.extend(_one_or_many(one, listed))
    seen = set()
    out = []
    for n in names:
        n = _strip_path(n)
        if not n or n in _NEXT_TOKENS or n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def resolve_read_args(args: dict, listed=None) -> tuple:
    args = args or {}
    listed = _listed_norm(listed)
    path, err = parse_index(args, listed)
    if err:
        return [], err
    if path:
        return [path], ""
    names = parse_filenames(args, listed=listed)
    return names, ""


def files_in_tool_text(text: str) -> list:
    text = text or ""
    names = []
    for m in re.finditer(re.escape(BATCH_MARK) + r"([^\]]+)】", text):
        names.extend(x.strip() for x in _SPLIT_NAMES.split(m.group(1)) if x.strip())
    names.extend(re.findall(r"【自动读取 ([^\s\]]+)", text))
    names.extend(re.findall(r"【本次读取的文件：([^】\n]+)】", text))
    seen = set()
    out = []
    for n in names:
        n = n.replace("\\", "/")
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def batch_header(files: list, extra: str = "") -> str:
    files = [str(f).replace("\\", "/") for f in (files or []) if f]
    mark = BATCH_MARK + "、".join(files) + "】"
    line = "本批共读 %d 个文件：%s。" % (len(files), "、".join(files))
    if extra:
        line += extra
    return mark + "\n" + line


class ProgressSession:
    """一份任务一份记录：已列出 / 已读 / 已保存 / 笔记 / 还没做完。"""

    def __init__(
        self,
        article_dir: str,
        goal: str = "",
        store_dir: Optional[str] = None,
        session_id: Optional[str] = None,
        user_text: str = "",
        resume=None,
    ):
        self.article_dir = os.path.abspath(article_dir or "")
        self.store_dir = store_dir or default_store()
        os.makedirs(self.store_dir, exist_ok=True)
        self.session_id = _safe_session_id(session_id) if session_id else ""
        self.path = progress_path(self.article_dir, self.store_dir, session_id=self.session_id or None)
        existing = load_progress(self.path)
        same_dir = (
            existing
            and os.path.normcase(existing.get("article_dir") or "")
            == os.path.normcase(self.article_dir)
        )
        text = (user_text or goal or "").strip()
        resumed = False
        if same_dir:
            self.data = existing
            if goal and not self.session_id:
                self.data["goal"] = _one_line(goal)
        elif self.session_id:
            prior, prior_path = load_latest_prior(self.article_dir, self.store_dir, exclude=self.path)
            want = False
            if resume is True:
                want = bool(prior)
            elif resume is False:
                want = False
            else:
                want = should_resume_prior(text, prior)
            if want and prior:
                self.data = json.loads(json.dumps(prior))
                self.data["article_dir"] = self.article_dir
                self.data["session_id"] = self.session_id
                self.data["resumed_from"] = prior_path
                resumed = True
                log.info("ollama 进度续做 session=%s prior=%s", self.session_id, prior_path)
            else:
                self.data = new_record(self.article_dir, goal)
                self.data["session_id"] = self.session_id
                log.info("ollama 进度新会话 session=%s（不注入上次记录）", self.session_id)
        else:
            self.data = new_record(self.article_dir, goal)
        self.data.setdefault("notes", [])
        self.data.setdefault("awaiting_notes", [])
        self.data.setdefault("written", {})
        self.data.setdefault("read", {})
        self.data.setdefault("listed", [])
        self.data.setdefault("steps", [])
        self.data.setdefault("task_card", {})
        self.data.setdefault("stage", "")
        self.data.setdefault("trace", [])
        self.data.setdefault("session_id", self.session_id)
        self.touched = False
        self.reread_keys = []
        self.reread_full_once = set()
        self.last_served = []
        self._refresh_stale()
        if same_dir or resumed:
            self.save()

    def save(self) -> None:
        self.data["schema"] = SCHEMA
        self.data["pending"] = self.unread()
        self.data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        atomic_save(self.path, self.data)

    def is_fresh(self) -> bool:
        d = self.data
        if d.get("resumed_from"):
            return False
        return not (d.get("steps") or d.get("read") or d.get("written") or d.get("notes") or d.get("renamed"))

    def fresh_task_text(self) -> str:
        card = format_task_card(self.data.get("task_card") or {})
        if not card:
            return ""
        order = list(self.data.get("list_index") or self.data.get("listed") or [])
        listing_preview = ""
        if order:
            items = [f'#{idx} path="{rel}"' for idx, rel in enumerate(order[:30], 1)]
            more = f" …（共 {len(order)} 项）" if len(order) > 30 else f"（共 {len(order)} 项）"
            listing_preview = "\n【当前工作目录清单" + more + "】\n" + "\n".join(items) + "\n（可直接用 read_article index=编号 读取，无需先调 list_articles）"
        return (
            FRESH_TAG + "\n"
            + card
            + listing_preview
        )

    def has_digest(self) -> bool:
        d = self.data
        return bool(d.get("listed") or d.get("read") or d.get("written") or d.get("notes") or d.get("task_card"))

    def mark_listed(self) -> list:
        files = []
        try:
            from utils.ollama_dirs import iter_text_file_rels
            files = list(iter_text_file_rels(self.article_dir))
        except Exception:
            try:
                names = os.listdir(self.article_dir)
            except OSError:
                return list(self.data.get("listed") or [])
            for name in sorted(names, key=_name_key):
                path = os.path.join(self.article_dir, name)
                if not os.path.isfile(path):
                    continue
                if os.path.splitext(name)[1].lower() not in TEXT_EXT:
                    continue
                files.append(name.replace("\\", "/"))
        listed = list(self.data.get("listed") or [])
        known = set(listed)
        for rel in files:
            if rel not in known:
                listed.append(rel)
                known.add(rel)
        self.data["listed"] = listed
        if not self.data.get("list_index"):
            try:
                from utils.ollama_dirs import list_index_names
                order = list_index_names(self.article_dir)
            except Exception:
                order = list(files)
            if order:
                self.data["list_index"] = order
        return listed

    def mark_read(
        self,
        rel: str,
        start: int = 0,
        got: int = 0,
        complete: bool = False,
        stamp: Optional[dict] = None,
        total: int = 0,
    ) -> None:
        rel = (rel or "").replace("\\", "/")
        if not rel:
            return
        rec = dict((self.data.setdefault("read", {})).get(rel) or {})
        end = start + max(0, int(got or 0))
        if complete:
            rec["read_to"] = int(total or end)
            rec["complete"] = True
            # Reading is not analysis; only save_notes records semantic memory.
        else:
            rec["read_to"] = max(int(rec.get("read_to") or 0), end)
            rec["complete"] = False
        rec["chars"] = rec["read_to"]
        if stamp:
            rec.update(stamp)
        self.data["read"][rel] = rec

    def mark_written(self, rel: str, chars: int = 0, path: str = "") -> None:
        rel = (rel or "").replace("\\", "/")
        if not rel:
            return
        rec = {}
        if path and os.path.isfile(path):
            rec = file_stamp(path)
        rec["chars"] = int(chars or rec.get("size") or 0)
        if path:
            rec["bytes"] = int(rec.get("size") or 0)
        self.data.setdefault("written", {})[rel] = rec
        self.reread_full_once.clear()
        self.reread_keys.clear()
        if _NOTES_NAME_RE.search(rel):
            self._cover_awaiting_as_notes(source="save:" + rel)

    def set_awaiting(self, files: list) -> None:
        names = [str(f).replace("\\", "/") for f in (files or []) if f]
        need = [n for n in names if self._is_complete(n) and not self.has_notes_for(n)]
        self.data["awaiting_notes"] = need
        self.save()

    def needs_notes(self) -> list:
        waiting = [r for r in (self.data.get("awaiting_notes") or []) if not self.has_notes_for(r)]
        if waiting != list(self.data.get("awaiting_notes") or []):
            self.data["awaiting_notes"] = waiting
        return waiting

    def unnoted_reads(self) -> list:
        out = []
        for rel, rec in (self.data.get("read") or {}).items():
            if int((rec or {}).get("read_to") or 0) <= 0:
                continue
            if self.has_notes_for(rel):
                continue
            out.append(rel)
        return sorted(out, key=_name_key)

    def notes_gate_files(self) -> list:
        """不再强制拦截读取新文件；后台已自动提取摘要。"""
        return []

    def notes_gate_text(self, files: list) -> str:
        shown = "、".join(files[:12]) + ("等 %d 个" % len(files) if len(files) > 12 else "")
        return (
            "【请先写笔记】这次没有读取新文件。已经读过、但还没写笔记的有：" + shown + "。"
            "请先调用 save_notes（files 填这些文件名），写下发现和下一步计划；或直接用 save_article 保存产出。"
        )

    def has_notes_for(self, rel: str) -> bool:
        rel = (rel or "").replace("\\", "/")
        rec = (self.data.get("read") or {}).get(rel) or {}
        end = int(rec.get("read_to") or 0)
        return bool(end and int(rec.get("noted_to") or 0) >= end)

    def can_compact_files(self, files: list, strict: bool = False) -> bool:
        names = [str(f).replace("\\", "/") for f in (files or []) if f]
        if not names:
            return True
        return all(self.has_notes_for(n) for n in names)

    def allow_reread(self, files: list) -> None:
        for f in files or []:
            self.reread_full_once.discard(str(f).replace("\\", "/"))

    def sources_complete(self) -> bool:
        listed = list(self.data.get("listed") or [])
        if not listed:
            reads = [r for r, rec in (self.data.get("read") or {}).items() if rec.get("complete")]
            return bool(reads) and not self.unread()
        return all(self._is_complete(r) for r in listed)

    def skill_outputs_saved(self) -> list:
        out = []
        for rel in self.data.get("written") or {}:
            if _NOTES_NAME_RE.search(rel):
                out.append(rel)
        return out

    def remaining_work(self) -> str:
        bits = []
        pending = self.unread()
        if pending:
            bits.append("尚未处理：" + "、".join(self._with_idx(r) for r in pending[:12]) + (" 等" if len(pending) > 12 else ""))
        written = list((self.data.get("written") or {}).keys())
        if written:
            bits.append("已保存：" + "；".join(written[:8]))
        if not bits:
            bits.append("请根据进度继续还没做完的部分（保存结果，或处理下一个输入）")
        return "；".join(bits)

    def notes_digest(self, limit: int = 800) -> str:
        items = self.data.get("notes") or []
        if not items:
            # 若无显式 save_notes，则汇总已读文件的自动摘录
            excerpts = []
            for rel, rec in (self.data.get("read") or {}).items():
                ex = (rec or {}).get("excerpt") or ""
                if ex:
                    excerpts.append(f"（{rel}）{ex[:160]}")
            if excerpts:
                return "\n".join(excerpts[:6])[:limit]
            return "尚无分析笔记。"
        parts = []
        used = 0
        for item in items:
            files = "、".join((item.get("files") or [])[:8])
            text = (item.get("text") or "").strip()
            block = "（" + files + "）" + text if files else text
            if used + len(block) > limit:
                block = block[: max(0, limit - used)] + "…"
            parts.append(block)
            used += len(block)
            if used >= limit:
                break
        return "\n".join(parts)

    def read_notes(self, start=0) -> str:
        try:
            start = max(0, int(start))
        except (ValueError, TypeError):
            return "start 必须是非负整数。"
        body = "\n\n".join("覆盖：" + "、".join(item.get("files") or []) + "\n" + str(item.get("text") or "") for item in self.data.get("notes") or [])
        chunk = body[start:start + 3000]
        if not chunk:
            return "笔记已读完。" if body else "尚无分析笔记。"
        end = start + len(chunk)
        return chunk + ("\n还有笔记，请调用 read_notes(start=%d)。" % end if end < len(body) else "\n笔记已读完。")

    def save_notes(self, content: str, files=None) -> str:
        text = (content or "").strip()
        if len(text) < MIN_NOTES:
            return (
                "没有保存笔记：内容太短，请写下这一步学到了什么、做了什么决定、下一步做什么"
                "（至少 %d 字），再调用 save_notes。" % MIN_NOTES
            )
        cover = []
        listed = list(self.data.get("list_index") or self.data.get("listed") or [])
        if isinstance(files, str):
            cover = _parts_from_files_string(files, listed)
        elif isinstance(files, (list, tuple)):
            cover = [_unique_listed_match(str(x), listed) or _strip_path(x) for x in files if _strip_path(x)]
        if not cover:
            cover = list(self.needs_notes() or self.unnoted_reads())
        if not cover:
            cover = list(self.completed_files())
        if not cover:
            cover = ["（未指定文件）"]
        norm = " ".join(text.split())
        for old in self.data.get("notes") or []:
            if " ".join(str(old.get("text") or "").split()) == norm:
                # Duplicate text must not mark newly read chunks as summarized.
                self.append_step("save_notes", "、".join(cover[:8]), digest="与已有笔记相同，未重复记下", ok=False)
                self.save()
                log.info("ollama 进度笔记重复，未追加 chars=%d", len(text))
                return "这条笔记和已记下的内容相同，没有重复记录。\n" + self.next_action_text()
        item = {
            "files": cover,
            "text": text,
            "chars": len(text),
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.data.setdefault("notes", []).append(item)
        for rel in cover:
            rec = self.data.setdefault("read", {}).setdefault(rel, {})
            rec["has_notes"] = True
            rec["noted_to"] = int(rec.get("read_to") or 0)
        waiting = [r for r in (self.data.get("awaiting_notes") or []) if r not in cover]
        self.data["awaiting_notes"] = waiting
        self.touched = True
        self.reread_full_once.clear()
        self.reread_keys.clear()
        self.append_step("save_notes", "、".join(cover[:8]), digest=text[:160])
        self._append_sidecar(item)
        self.save()
        log.info("ollama 进度笔记 files=%s chars=%d", ",".join(cover[:8]), len(text))
        return "已记下笔记：覆盖 " + "、".join(cover[:20]) + "（%d 字）。" % len(text) + "\n" + self.remaining_work()

    def append_step(self, tool: str, target: str = "", digest: str = "", ok: bool = True) -> None:
        steps = self.data.setdefault("steps", [])
        rec = {
            "tool": tool or "",
            "target": (target or "")[:200],
            "digest": _one_line(digest)[:240],
            "ok": bool(ok),
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if steps:
            last = steps[-1]
            if last.get("tool") == rec["tool"] and last.get("target") == rec["target"] and last.get("digest") == rec["digest"]:
                return
        steps.append(rec)
        if len(steps) > 200:
            self.data["steps"] = steps[-200:]

    def after_tool(self, name: str, args: dict, result: str, spoken: str = "") -> None:
        text = result or ""
        head = text.lstrip()
        if name == "save_notes":
            return
        if name == "list_articles":
            if head.startswith(_LIST_FAIL):
                self.append_step("list_articles", _filename_of(args) or self.article_dir,
                                 digest=(head.splitlines() or [""])[0][:160], ok=False)
                self.touched = True
                self.save()
                return
            self.mark_listed()
            self.append_step("list_articles", self.article_dir, digest=(head.splitlines() or [""])[0][:80])
            self.touched = True
            self.save()
            return
        if name in ("web_search", "web_fetch"):
            args = args or {}
            target = str(args.get("url") or args.get("query") or args.get("q") or "").strip()
            ok = not head.startswith(("搜索失败", "打开网页失败", "只能打开", "没有"))
            if ok:
                self.append_step(name, target, digest=head[:160], ok=True)
                self.touched = True
                self.save()
            return
        if name in ("read_article", "read_unread_batch"):
            try:
                from utils.ollama_agent import _tool_failed as _agent_failed
                failed = _agent_failed(text, name)
            except Exception:
                failed = False
            if head.startswith(("工作目录：", "文件夹：")):
                self.append_step(name, _filename_of(args) or "（文件夹）",
                                 digest="这是文件夹：已返回其中的文件清单，没有读正文", ok=True)
                self.touched = True
                self.save()
                return
            if failed or head.startswith(_READ_FAIL) or head.startswith(("【已有记录】", "【不必再读】", "【请先写笔记】")):
                target = _filename_of(args)
                if not target and (args or {}).get("index") not in (None, ""):
                    target = "index=" + str((args or {}).get("index"))
                if head.startswith(("【已有记录】", "【不必再读】")):
                    dg = "已读过且未改动：返回记录摘要，没有重读全文"
                else:
                    dg = (head.splitlines() or [""])[0][:160]
                self.append_step(name, target, digest=dg, ok=False)
                self.touched = True
                self.save()
                return
            rels = parse_filenames(args, listed=self.data.get("list_index") or self.data.get("listed")) or files_in_tool_text(text)
            if not rels:
                rel = _filename_of(args)
                if rel:
                    rels = [rel]
            if not rels:
                rels = [r for r in (getattr(self, "last_served", None) or []) if r]
            if len(rels) <= 1:
                rel = rels[0] if rels else ""
                if rel:
                    self._mark_one_read(rel, args, text)
                    self._store_excerpt(rel, text)
                    self.append_step("read_article", rel, digest=self._short_digest(rel, text))
            else:
                for rel in rels:
                    if "请再次调用" in text and rel in text:
                        self._mark_one_read(rel, {"filename": rel, "start": self.next_start(rel)}, text)
                    else:
                        path = os.path.join(self.article_dir, rel.replace("/", os.sep))
                        stamp = file_stamp(path) if os.path.isfile(path) else {}
                        if "请再次调用" not in text:
                            self.mark_read(rel, start=0, got=int((stamp or {}).get("size") or 1), complete=True, stamp=stamp)
                        else:
                            self._mark_one_read(rel, {"filename": rel}, text)
            self.touched = True
            self.save()
            return
        if name == "rename_article":
            self._after_rename(text)
            return
        if name == "save_article":
            if not head.startswith("已保存："):
                return
            m = _SAVED_RE.search(head.split("\n", 1)[0])
            saved_path = m.group(1).strip() if m else ""
            cm = _SAVED_CHARS_RE.search(head.split("\n", 1)[0])
            chars = int(cm.group(1)) if cm else 0
            if saved_path:
                try:
                    rel = rel_path(self.article_dir, saved_path)
                except ValueError:
                    rel = os.path.basename(saved_path)
            else:
                rel = _filename_of(args)
            self.mark_written(rel, chars=chars, path=saved_path)
            self.data["written"][rel]["complete"] = (args or {}).get("complete", True) is not False
            self.append_step("save_article", rel, digest=head.splitlines()[0][:160] if head.splitlines() else rel)
            self.touched = True
            self.save()

    def _mark_one_read(self, rel: str, args: dict, text: str) -> None:
        start = _read_start(args)
        path = os.path.join(self.article_dir, rel.replace("/", os.sep))
        stamp = file_stamp(path) if os.path.isfile(path) else {}
        text = strip_read_header(text)
        head = (text or "").lstrip()
        if head.startswith("已经读到末尾"):
            self.mark_read(rel, start=start, got=0, complete=True, stamp=stamp)
            return
        m = _READ_TO_RE.search(text or "")
        if m:
            start = int(m.group(1))
            end = int(m.group(2))
            complete = "请再次调用" not in (text or "")
            self.mark_read(
                rel, start=start, got=end - start,
                complete=complete, stamp=stamp, total=end if complete else 0,
            )
            return
        body = text or ""
        self.mark_read(
            rel, start=start, got=len(body),
            complete=True, stamp=stamp, total=start + len(body),
        )

    def _real_file(self, rel: str) -> bool:
        return os.path.isfile(os.path.join(self.article_dir, str(rel or "").replace("/", os.sep)))

    def unread(self) -> list:
        names = list(self.data.get("listed") or [])
        for rel in (self.data.get("read") or {}):
            if rel not in names and self._real_file(rel):
                names.append(rel)
        return [rel for rel in sorted(names, key=_name_key) if not self._is_complete(rel)]

    def completed_files(self) -> list:
        names = list(self.data.get("listed") or [])
        for rel in (self.data.get("read") or {}):
            if rel not in names:
                names.append(rel)
        return [rel for rel in sorted(names, key=_name_key) if self._is_complete(rel)]

    def next_start(self, rel: str) -> int:
        rec = (self.data.get("read") or {}).get(rel.replace("\\", "/")) or {}
        if rec.get("complete") and self._is_complete(rel):
            return 0
        return int(rec.get("read_to") or 0)

    def is_unchanged_complete(self, rel: str) -> bool:
        return self._is_complete(rel)

    def expand_batch(self, start_names: Optional[list] = None) -> list:
        pending = self.unread()
        if not pending:
            return []
        if start_names:
            start = None
            for name in start_names:
                if name in pending:
                    start = name
                    break
            if start is None:
                return [n for n in start_names if n in pending] or []
            pending = pending[pending.index(start):]
        return pending[:BATCH_FILES]

    def gate_read(self, args: dict) -> dict:
        """一次点一个就只读一个。已读未改动的允许再要一次全文；再循环重读则回摘要。取消强制笔记拦截。"""
        args = args or {}
        listed = list(self.data.get("list_index") or self.data.get("listed") or [])
        if not listed:
            try:
                from utils.ollama_dirs import list_index_names
                listed = list_index_names(self.article_dir)
            except Exception:
                listed = []
        requested, err = resolve_read_args(args, listed)
        if err:
            return {"action": "refuse", "files": [], "message": err}
        start = _read_start(args)

        if start > 0 and len(requested) == 1:
            rel = requested[0]
            if not self.is_unchanged_complete(rel):
                return {"action": "read", "files": [rel], "message": ""}

        if not requested:
            nxt = (self.unread() or [None])[0]
            if not nxt:
                return {
                    "action": "digest",
                    "files": [],
                    "message": "【已有记录】当前没有尚未处理的文件。\n" + self.remaining_work(),
                }
            return {"action": "read", "files": [nxt], "message": ""}

        fresh, old = [], []
        for name in requested:
            if self.is_unchanged_complete(name):
                old.append(name)
            else:
                fresh.append(name)

        if old and not fresh:
            allow, deny = [], []
            for rel in old:
                if rel not in self.reread_full_once:
                    allow.append(rel)
                    self.reread_full_once.add(rel)
                    self._note_reread([rel])
                else:
                    deny.append(rel)
                    self._note_reread([rel])
            if allow and not deny:
                return {"action": "read", "files": allow, "message": ""}
            if allow and deny:
                return {
                    "action": "read",
                    "files": allow,
                    "message": self._already_read_notice(deny) + "\n",
                }
            return {
                "action": "digest",
                "files": deny,
                "message": self._already_read_notice(deny, args),
            }

        return {"action": "read", "files": fresh, "message": self._skip_old_line(old)}

    def should_auto_continue(self) -> bool:
        return False

    def should_steer_notes(self) -> bool:
        return False

    def should_steer_write(self) -> bool:
        return False

    def steer_notes_text(self) -> str:
        return (
            "进度已由程序记下。请继续处理未完成的文件或使用 save_article 保存产出。"
        )

    def steer_write_text(self) -> str:
        return "请根据已有进度继续还没做完的部分，不必把已处理且未改动的输入再做一遍。"

    def maybe_capture_spoken_notes(self, spoken: str) -> str:
        text = (spoken or "").strip()
        if len(text) < 80:
            return ""
        if _looks_like_ack(text):
            return ""
        return self.save_notes(text, files=list(self.needs_notes() or self.completed_files() or ["（本步）"]))

    def ensure_task_card(self, user_text: str = "", listed=None) -> dict:
        text = (user_text or "").strip()
        existing = dict(self.data.get("task_card") or {})
        listed = listed if listed is not None else list(self.data.get("listed") or [])
        if existing.get("goal") and _is_continue_message(text):
            self._sync_rename_only(existing)
            return existing
        card = extract_task_card(text or existing.get("user_input") or self.data.get("goal") or "", listed, self.article_dir)
        if existing.get("goal") and not text:
            self._sync_rename_only(existing)
            return existing
        if (
            existing.get("goal") == card.get("goal")
            and existing.get("outputs") == card.get("outputs")
            and existing.get("success") == card.get("success")
        ):
            if listed and not existing.get("input"):
                self.data["task_card"] = card
                self._sync_rename_only(card)
                self.save()
            else:
                self._sync_rename_only(self.data.get("task_card") or card)
            return self.data.get("task_card") or card
        self.data["task_card"] = card
        if card.get("goal"):
            self.data["goal"] = card["goal"]
        self._sync_rename_only(card)
        self.touched = True
        self.append_trace("感知", "锁定任务卡：" + (card.get("goal") or ""))
        self.save()
        return card

    def _sync_rename_only(self, card=None) -> None:
        card = card or (self.data.get("task_card") or {})
        flag = bool((card or {}).get("rename_only"))
        if not flag:
            try:
                from utils.ollama_intent import is_rename_only_task
                text = (card or {}).get("user_input") or (self.data.get("goal") or "")
                flag = is_rename_only_task(text)
            except Exception:
                flag = False
        self.data["rename_only"] = bool(flag)

    def _after_rename(self, text: str) -> None:
        import re as _re
        moved = []
        for m in _re.finditer(r"^\s*(?:已重命名：)?(.+?) → (.+)$", text or "", _re.M):
            old = m.group(1).strip().strip("：:")
            new = m.group(2).strip().split("；", 1)[0].strip()
            if old.startswith("已重命名"):
                continue
            if old and new:
                moved.append((old.replace("\\", "/"), new.replace("\\", "/")))
        bag = self.data.setdefault("renamed", {})
        for old, new in moved:
            bag[old] = new
            read = self.data.get("read") or {}
            written = self.data.get("written") or {}
            if old in read and new not in read:
                read[new] = read.pop(old)
            elif old in read:
                read.pop(old, None)
            if old in written and new not in written:
                written[new] = written.pop(old)
            elif old in written:
                written.pop(old, None)
            listed = list(self.data.get("listed") or [])
            listed = [new if x == old else x for x in listed]
            if new not in listed:
                listed.append(new)
            self.data["listed"] = [x for x in listed if x != old or x == new]
        self.refresh_listed()
        self.append_step("rename_article", ",".join(n for _o, n in moved) or "rename", digest=(text or "").splitlines()[0][:160] if text else "")
        self.touched = True
        self.save()

    def refresh_listed(self) -> list:
        try:
            from utils.ollama_dirs import iter_text_file_rels
            present = list(iter_text_file_rels(self.article_dir))
        except Exception:
            present = []
            try:
                names = os.listdir(self.article_dir)
            except OSError:
                names = []
            for name in names:
                path = os.path.join(self.article_dir, name)
                if os.path.isfile(path) and os.path.splitext(name)[1].lower() in TEXT_EXT:
                    present.append(name.replace("\\", "/"))
        present_set = set(present)
        old = list(self.data.get("listed") or [])
        listed = [x for x in old if x in present_set]
        for rel in present:
            if rel not in listed:
                listed.append(rel)
        self.data["listed"] = listed
        try:
            from utils.ollama_dirs import list_index_names
            self.data["list_index"] = list_index_names(self.article_dir)
        except Exception:
            self.data["list_index"] = list(listed)
        return listed

    def task_card(self) -> dict:
        return dict(self.data.get("task_card") or {})

    def task_card_text(self) -> str:
        return format_task_card(self.task_card())

    def required_outputs(self) -> list:
        return [str(x).replace("\\", "/") for x in (self.task_card().get("outputs") or []) if x]

    def required_unread(self) -> list:
        card = self.task_card()
        if not card.get("requires_all_sources"):
            return []
        return [n for n in card.get("source_files", []) if not self._is_complete(n)]

    def success_met(self) -> bool:
        if self.required_unread():
            return False
        if self.data.get("rename_only") or (self.task_card() or {}).get("rename_only"):
            renamed = self.data.get("renamed") or {}
            if not renamed:
                return False
            try:
                from utils.ollama_dirs import han_digit_stem
            except Exception:
                return bool(renamed)
            listed = list(self.data.get("listed") or [])
            if not listed:
                return bool(renamed)
            for rel in listed:
                stem, ext = os.path.splitext(os.path.basename(rel))
                if han_digit_stem(stem) != stem:
                    return False
            return True
        need = self.required_outputs()
        if not need:
            return False
        written = self.data.get("written") or {}
        for rel in need:
            rec = written.get(rel)
            if rec is None:
                for key, val in written.items():
                    k = str(key).replace("\\", "/")
                    if k == rel or k.endswith("/" + rel) or k.split("/")[-1] == rel.split("/")[-1]:
                        rec = val
                        break
            if not rec or int(rec.get("chars") or 0) <= 0 or rec.get("complete") is False:
                return False
        return True

    def missing_outputs(self) -> list:
        if not self.required_outputs():
            return []
        if self.success_met():
            return []
        written = self.data.get("written") or {}
        missing = []
        for rel in self.required_outputs():
            ok = False
            for key, val in written.items():
                k = str(key).replace("\\", "/")
                if k == rel or k.endswith("/" + rel) or k.split("/")[-1] == rel.split("/")[-1]:
                    if int((val or {}).get("chars") or 0) > 0 and (val or {}).get("complete") is not False:
                        ok = True
                        break
            if not ok:
                missing.append(rel)
        return missing

    def set_stage(self, stage: str, detail: str = "") -> None:
        self.data["stage"] = (stage or "").strip()
        if detail or stage:
            self.append_trace(stage, detail)

    def append_trace(self, stage: str, detail: str = "") -> None:
        tr = self.data.setdefault("trace", [])
        tr.append({
            "stage": (stage or "").strip(),
            "detail": _one_line(detail)[:200],
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        if len(tr) > 200:
            self.data["trace"] = tr[-200:]

    def success_stop_text(self) -> str:
        outs = self.required_outputs()
        if outs:
            return "成功标准已满足：已保存 " + "、".join(outs) + "（均有正文）。请简短收尾，不要再调用工具。"
        return "成功标准已满足。请简短收尾，不要再调用工具。"

    def digest(self) -> str:
        d = self.data
        lines = []
        card = format_task_card(d.get("task_card") or {})
        if card:
            lines.append(card)
        if d.get("article_dir"):
            lines.append("工作目录：" + d["article_dir"])
        if d.get("goal") and not (d.get("task_card") or {}).get("goal"):
            lines.append("任务：" + d["goal"])
        elif d.get("goal") and not card:
            lines.append("任务：" + d["goal"])
        listed = d.get("listed") or []
        if listed:
            lines.append(f"已列出：{len(listed)} 个文件")
        done = self.completed_files()
        if done:
            show = "、".join(done[:12])
            if len(done) > 12:
                show += f" 等共 {len(done)} 个"
            else:
                show += f"（共 {len(done)} 个）"
            lines.append("已读完：" + show)
        pending = self.unread()
        if pending:
            bits = []
            for rel in pending[:12]:
                rec = (d.get("read") or {}).get(rel) or {}
                if rec.get("read_to"):
                    bits.append(f"{self._with_idx(rel)}（已读到第 {rec['read_to']} 字）")
                else:
                    bits.append(self._with_idx(rel))
            extra = f" 等共 {len(pending)} 个" if len(pending) > 12 else f"（共 {len(pending)} 个）"
            lines.append("未读完：" + "、".join(bits) + extra)
        elif listed or d.get("read"):
            lines.append("未读完：无")
        notes = d.get("notes") or []
        if notes:
            lines.append("已有笔记：%d 条（%d 字）；用 read_notes(start=0) 可分页读取全文。" % (len(notes), sum(int(n.get("chars") or 0) for n in notes)))
            excerpt = self.notes_digest(600)
            if excerpt and excerpt != "尚无分析笔记。":
                lines.append("笔记要点：\n" + excerpt)
        written = d.get("written") or {}
        if written:
            bits = []
            for rel, rec in written.items():
                ch = rec.get("chars")
                bits.append(f"{rel}（{ch} 字）" if ch else rel)
            lines.append("已保存：" + "；".join(bits[:20]))
        missing = self.missing_outputs()
        if missing:
            lines.append("还欠（成功标准要求、尚未保存）：" + "、".join(missing[:10]))
        elif self.success_met():
            lines.append("成功标准：已满足")
        steps = d.get("steps") or []
        if steps:
            recent = steps[-8:]
            bits = []
            for s in recent:
                bit = (s.get("tool") or "") + ((" " + s.get("target")) if s.get("target") else "")
                if s.get("digest"):
                    bit += "：" + s.get("digest")
                bits.append(bit)
            lines.append("最近步骤：\n" + "\n".join(bits))
        if d.get("updated"):
            lines.append("记录时间：" + d["updated"])
        return "\n".join(lines) if lines else "尚无进度。"

    def status_line(self) -> str:
        listed = self.data.get("listed") or []
        n_done = sum(1 for r in listed if self._is_complete(r))
        if not listed:
            n_done = sum(1 for r in (self.data.get("read") or {}) if self._is_complete(r))
            total = n_done + len(self.unread())
        else:
            total = len(listed)
        n_write = len(self.data.get("written") or [])
        n_notes = len(self.data.get("notes") or [])
        stage = (self.data.get("stage") or "").strip()
        line = f"进度：已读 {n_done}/{total}，笔记 {n_notes} 条，已保存 {n_write} 个文件"
        return (stage + " · " + line) if stage else line

    def auto_read_batch(
        self,
        read_fn=None,
        budget=None,
        read_chars: int = 0,
    ) -> str:
        return ""

    def _is_complete(self, rel: str) -> bool:
        rel = (rel or "").replace("\\", "/")
        rec = (self.data.get("read") or {}).get(rel)
        if not rec or not rec.get("complete"):
            return False
        path = os.path.join(self.article_dir, rel.replace("/", os.sep))
        if not os.path.isfile(path):
            rec["complete"] = False
            return False
        stamp = file_stamp(path)
        if rec.get("sha256") and stamp.get("sha256") and stamp.get("sha256") != rec.get("sha256"):
            rec["complete"] = False
            rec["read_to"] = 0
            rec["noted_to"] = 0
            rec["has_notes"] = False
            return False
        if rec.get("size") and stamp.get("size") and stamp.get("size") != rec.get("size"):
            rec["complete"] = False
            rec["read_to"] = 0
            rec["noted_to"] = 0
            rec["has_notes"] = False
            return False
        if (
            not rec.get("sha256")
            and rec.get("mtime")
            and stamp.get("mtime")
            and stamp.get("mtime") != rec.get("mtime")
        ):
            rec["complete"] = False
            rec["read_to"] = 0
            rec["noted_to"] = 0
            rec["has_notes"] = False
            return False
        return True

    def _refresh_stale(self) -> None:
        changed = False
        for rel in list((self.data.get("read") or {}).keys()):
            rec = self.data["read"][rel]
            if not rec.get("complete"):
                continue
            if not self._is_complete(rel):
                changed = True
        if changed:
            log.info("ollama 进度：有文件已改动，将重新读取")

    def _cover_awaiting_as_notes(self, source: str = "") -> None:
        waiting = list(self.needs_notes())
        if not waiting:
            return
        for rel in waiting:
            rec = self.data.setdefault("read", {}).setdefault(rel, {})
            rec["has_notes"] = True
        self.data.setdefault("notes", []).append({
            "files": waiting,
            "text": "（成果文件已覆盖本批：" + source + "）",
            "chars": 0,
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        self.data["awaiting_notes"] = []

    def _append_sidecar(self, item: dict) -> None:
        path = os.path.splitext(self.path)[0] + ".notes.md"
        block = (
            "\n\n## %s\n\n覆盖：%s\n\n%s\n"
            % (item.get("at") or "", "、".join(item.get("files") or []), item.get("text") or "")
        )
        try:
            prev = ""
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    prev = f.read()
            fd, tmp = tempfile.mkstemp(prefix=".notes-", suffix=".tmp", dir=os.path.dirname(path) or ".")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(prev)
                    f.write(block)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
            except Exception:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
        except OSError as e:
            log.info("ollama 笔记副本未能写入：%s", e)

    def _note_reread(self, files: list) -> None:
        key = tuple(sorted(str(f).replace("\\", "/") for f in (files or []) if f))
        if key:
            self.reread_keys.append(key)

    def _reread_looping(self) -> bool:
        if len(self.reread_keys) >= 2:
            return True
        return False

    def _index_of(self, rel: str) -> int:
        order = [str(x).replace("\\", "/") for x in (self.data.get("list_index") or [])]
        rel = (rel or "").replace("\\", "/")
        return order.index(rel) + 1 if rel in order else 0

    def _with_idx(self, rel: str) -> str:
        k = self._index_of(rel)
        return "%s（index=%d）" % (rel, k) if k else rel

    def next_action_text(self) -> str:
        pending = self.unread()
        if pending:
            rel = pending[0]
            k = self._index_of(rel)
            how = "read_article index=%d" % k if k else 'read_article filename="%s"' % rel
            return (
                "下一个还没读的文件是「%s」：要继续读就调用 %s；"
                "如果已经读够，请直接用 save_article 保存结果。" % (rel, how)
            )
        missing = self.missing_outputs()
        if missing:
            return "输入都已读完。下一步：用 save_article 保存 " + "、".join(missing[:5]) + "（content 放该文件完整正文）。"
        if self.required_outputs():
            return "输入都已读完，必要结果也已保存。请直接给出最终回答并结束，不要再调用工具。"
        return "输入都已读完。请根据记录完成任务：需要写文件就用 save_article 保存，否则直接给出最终回答并结束。"

    def progress_mark(self) -> tuple:
        d = self.data
        read = tuple(sorted(
            (k, int((v or {}).get("read_to") or 0), bool((v or {}).get("complete")), bool((v or {}).get("has_notes")))
            for k, v in (d.get("read") or {}).items()
        ))
        written = tuple(sorted(
            (k, int((v or {}).get("chars") or 0), str((v or {}).get("sha256") or ""))
            for k, v in (d.get("written") or {}).items()
        ))
        notes = tuple(" ".join(str(n.get("text") or "").split())[:200] for n in (d.get("notes") or []))
        renamed = tuple(sorted((d.get("renamed") or {}).items()))
        web = tuple(sorted({
            str(s.get("target") or "") for s in (d.get("steps") or [])
            if s.get("tool") in ("web_search", "web_fetch") and s.get("ok")
        }))
        return (len(d.get("listed") or []), read, written, notes, renamed, web)

    def _already_read_notice(self, files: list, args: Optional[dict] = None) -> str:
        names = "、".join(files[:20])
        notes = self.notes_digest(800)
        excerpts = []
        for rel in files[:5]:
            rec = (self.data.get("read") or {}).get(rel) or {}
            ex = (rec.get("excerpt") or "").strip()
            if ex:
                excerpts.append(rel + "：" + ex)
        body = "【已有记录】这些内容已处理过且未改动，不再反复返回全文：" + names + "。\n"
        raw_idx = (args or {}).get("index")
        if raw_idx in (None, ""):
            raw_idx = (args or {}).get("id")
        if raw_idx not in (None, "") and files:
            body += (
                "你请求的 index=%s 对应「%s」，它已经读过。index 是清单编号，不一定等于章节号。\n"
                % (str(raw_idx).strip(), files[0])
            )
        body += "已有笔记：\n" + notes + "\n"
        if excerpts:
            body += "记录中的要点摘录：\n" + "\n".join(excerpts) + "\n"
        body += self.remaining_work()
        body += "\n" + self.next_action_text()
        body += "\n请根据记录继续后面的工作，不要再循环重读同一批未改动的内容。"
        return body

    def _notes_first_message(self, requested: list) -> str:
        return self.steer_notes_text()

    def _all_recorded_body(self) -> str:
        n = len(self.completed_files())
        return (
            "已有 %d 项输入记入进度。请根据记录继续还没做完的部分，不必把未改动的内容再处理一遍。\n"
            "已有笔记：\n%s\n%s"
            % (n, self.notes_digest(800), self.remaining_work())
        )

    def _all_recorded_message(self) -> str:
        return "【不必再读】" + self._all_recorded_body()

    def _loop_break_message(self, files: list) -> str:
        names = "、".join(files[:20])
        return (
            "【已有记录】这些内容已经反复读取，且都已记入进度、未改动：" + names + "。\n"
            + self.remaining_work()
            + "\n请根据记录继续后面的工作（保存结果，或处理还没做的输入）。"
        )

    def _skip_old_line(self, old: list) -> str:
        if not old:
            return ""
        return "【已有记录】以下文件已读过且未改动，本批跳过、不回全文：" + "、".join(old[:20]) + "。\n"

    def _store_excerpt(self, rel: str, text: str) -> None:
        rel = (rel or "").replace("\\", "/")
        if not rel:
            return
        rec = self.data.setdefault("read", {}).setdefault(rel, {})
        rec["excerpt"] = _excerpt_of(strip_read_header(text))

    def _short_digest(self, rel: str, text: str) -> str:
        rec = (self.data.get("read") or {}).get(rel) or {}
        n = rec.get("chars") or rec.get("read_to") or len(text or "")
        return "%s（%s 字）" % (rel, n)


def _excerpt_of(text: str, limit: int = 400) -> str:
    body = text or ""
    cut = body.find("\n…（")
    if cut > 0:
        body = body[:cut]
    if body.startswith("【批次文件：") or body.startswith("----- "):
        parts = body.split("\n", 4)
        body = parts[-1] if parts else body
    body = body.strip()
    if len(body) > limit:
        return body[:limit] + "…"
    return body


def _looks_like_ack(text: str) -> bool:
    text = (text or "").strip()
    if len(text) > 160:
        return False
    keys = ("已读完", "让我继续读", "开始读取", "内容已读取", "已完整读取", "让我再读", "让我重新读")
    return any(k in text for k in keys)


def _read_start(args: dict) -> int:
    raw = (args or {}).get("start")
    if raw is None:
        raw = (args or {}).get("offset")
    try:
        n = int(raw or 0)
    except (TypeError, ValueError):
        n = 0
    return max(0, n)