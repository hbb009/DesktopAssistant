# utils/ollama_auto.py
# ---------------------------------------------------------------------------
# 让程序自己做决定，不让用户去调参数：
#   1. 上下文长度：按模型能力（/api/show）、已加载的长度（/api/ps）和这一轮任务的大小自动选。
#      若模型已在显存/内存中按指定长度加载（如 30720），优先锁定该长度，避免 30GB+ 模型反复重载。
#   2. 网页：用户消息里有网址时，程序自己把整页正文抓下来（去掉导航/广告/脚本/相关推荐，
#      小说站同一章的「下一页」会接着抓），写成干净的文本。
#   3. 保存原文类请求直接落盘，不经过模型；改写/翻译/总结等交给分段驱动。
# 纯函数，不碰 Qt。
# ---------------------------------------------------------------------------
from __future__ import annotations

import gc
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests

from utils.logger import get_logger

log = get_logger("deskassist.ollama")

AUTO_MIN = 8192          # 最少给这么多
AUTO_DEFAULT = 30720     # 未加载时的默认稳态上下文（兼顾 24G/32G 显存与长任务）
AUTO_CEILING = 65536     # 按需要自动放大上下文的上限；不超过模型本身能力，也不一次占满显存
AUTO_STEP = 2048
OUTPUT_RESERVE = 4096    # 普通对话预留给回答的 token
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_INFO_CACHE: dict = {}
_LOCK = threading.Lock()


# ── 与 Ollama 通信（测试里可以替换这个函数）──────────────────────
def fetch_json(base: str, path: str, payload: Optional[dict] = None, timeout: float = 3.0):
    url = base.rstrip("/") + path
    sess = requests.Session()
    try:
        if payload is None:
            r = sess.get(url, timeout=timeout)
        else:
            r = sess.post(url, json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json()
    finally:
        sess.close()


def model_max_ctx(base: str, model: str) -> int:
    """模型本身支持的最大上下文（/api/show 的 *.context_length）。查不到返回 0。缓存。"""
    key = (base, model)
    with _LOCK:
        if key in _INFO_CACHE:
            return _INFO_CACHE[key]
    n = 0
    try:
        d = fetch_json(base, "/api/show", {"model": model}) or {}
        for k, v in (d.get("model_info") or {}).items():
            if str(k).endswith(".context_length"):
                try:
                    n = max(n, int(v))
                except Exception:
                    pass
        if not n:
            m = re.search(r"num_ctx\s+(\d+)", str(d.get("parameters") or ""))
            if m:
                n = int(m.group(1))
    except Exception as e:
        log.info("ollama 读取模型信息失败 %s：%s", model, e)
        return 0   # 失败不缓存，下次再试
    with _LOCK:
        _INFO_CACHE[key] = n
    return n


def loaded_ctx(base: str, model: str) -> int:
    """模型当前已按多大的上下文加载在内存里（/api/ps）。没加载或查不到返回 0。"""
    try:
        d = fetch_json(base, "/api/ps") or {}
    except Exception:
        return 0
    target = (model or "").strip().lower()
    target_tag = target if ":" in target.split("/")[-1] else target + ":latest"
    for m in d.get("models") or []:
        for cand in (m.get("name"), m.get("model")):
            c = str(cand or "").strip().lower()
            if not c:
                continue
            c_tag = c if ":" in c.split("/")[-1] else c + ":latest"
            if c == target or c_tag == target_tag or target in c:
                try:
                    return int(m.get("context_length") or 0)
                except Exception:
                    return 0
    return 0


def clear_cache() -> None:
    with _LOCK:
        _INFO_CACHE.clear()


def ceiling(max_ctx: int, loaded: int) -> int:
    """这一次最多用到多大：已加载长度是复用起点，不是硬上限；按模型能力和配置限制增长。"""
    top = max(AUTO_CEILING, loaded or 0)
    if max_ctx:
        top = min(top, max_ctx)
    return max(min(AUTO_MIN, max_ctx or AUTO_MIN), top)


def choose_ctx(need_tokens: int, max_ctx: int = 0, loaded: int = 0, floor: int = 0) -> int:
    """按需要的 token 数选上下文：
    1. 若模型已加载（loaded >= AUTO_MIN，例如 30720），且未显式要求超出 loaded 的 floor，
       仅在需求和输出预留均能容纳时复用 loaded，否则升档。
    2. 若模型尚未加载，使用固定档位（16384 / 30720 / 65536），避免每轮因字数微增而频繁改变 num_ctx。
    """
    if loaded and loaded >= AUTO_MIN and (not max_ctx or loaded <= max_ctx):
        if max(int(need_tokens * 1.2) + 1024, int(floor or 0)) <= loaded:
            return loaded
    want = int(max(0, need_tokens) * 1.2) + 1024
    want = max(AUTO_MIN, want, int(floor or 0))
    # 固定分档，避免每轮 2048 步进触发 llamacpp 重建 KV Cache
    for tier in (16384, AUTO_DEFAULT, 32768, 65536):
        if want <= tier:
            want = tier
            break
    else:
        want = ((want + AUTO_STEP - 1) // AUTO_STEP) * AUTO_STEP
    top = ceiling(max_ctx, loaded)
    n = min(want, top)
    if loaded and (not max_ctx or loaded <= max_ctx):
        n = max(n, loaded)
    return n


def escalate(cur: int, max_ctx: int = 0, loaded: int = 0) -> int:
    """放大一档（到上限为止）。若已加载模型锁在 loaded，则不超过 ceiling。"""
    return min(max(cur * 2, AUTO_MIN), ceiling(max_ctx, loaded)) if cur else AUTO_MIN


# ── 识别请求 ───────────────────────────────────────────────
URL_RE = re.compile(r"https?://[^\s<>\"'，。；！？、）】」』]+|www\.[^\s<>\"'，。；！？、）】」』]+", re.I)
SAVE_RE = re.compile(r"保存|存到|存进|存下|存入|存成|存为|下载|另存|落盘|抓取|扒下|扒取|写入工作目录|放到工作目录")
_FULL_RE = re.compile(r"完整|全部|全文|原文|整章|整篇|原样")
# 先打开/看看/进章节：即使句子里有「保存」，也不是「把当前这一页原文落盘」。
NAV_RE = re.compile(
    r"进入|点进|点开|打开(里面|其中|这个?链接|章节)|分别|"
    r"各(个)?章节|章节(里|中|内容|链接)|目录(页|里|中)的|"
    r"看到了什么|看见了什么|你看到|看得到什么|看到了啥|"
    r"这是什么(网站|网页|页面)|网页(上|里)有|页面(上|里)有|"
    r"介绍一下这"
)


def find_url(text: str) -> str:
    m = URL_RE.search(text or "")
    if not m:
        return ""
    u = m.group(0).rstrip(".,;:!?)]}")
    return u if u.lower().startswith("http") else "https://" + u


def wants_page_nav(text: str) -> bool:
    """要先打开页面、看目录或进入章节，而不是把当前这一页存成文件。"""
    return bool(NAV_RE.search(text or ""))


def is_save_original(text: str, task_re) -> bool:
    """「把当前这一页原文落盘」。进章节、看目录、改写翻译都不算。"""
    t = text or ""
    if not SAVE_RE.search(t) or task_re.search(t):
        return False
    if wants_page_nav(t):
        return False
    return True


def is_save_like(text: str) -> bool:
    return bool(SAVE_RE.search(text or ""))


# 改扩展名，不是改正文。总结、翻译、改写仍走原来的分段处理。
_CONVERT_TXT_RE = re.compile(
    r"(转成|转为|转换成|转换为|另存为|保存为|导出为|导出成|改成|改为).{0,16}(\.?txt|纯文本|文本文件)",
    re.I,
)
_REWRITE_RE = re.compile(
    r"总结|摘要|概括|提炼|翻译|译成|译为|改写|润色|重写|扩写|缩写|精简|校对|纠错|仿写|续写|改编|降重|删减"
)


def is_convert_to_txt(text: str) -> bool:
    """把已有文件原样改成 .txt。句子里同时要求总结或翻译时不算。"""
    t = text or ""
    if not _CONVERT_TXT_RE.search(t):
        return False
    return not _REWRITE_RE.search(t)


# 「打开目录，进入其中 N 章，把正文存下来」。总结、翻译、改写不算。
# 中间不能吃掉数字，否则「255」只会留下最后一位。
_ENTER_N_RE = re.compile(r"(?:进入|点进|点开|分别)[^\d]{0,16}(\d+)\s*个?\s*章")
_N_CHAPTERS_RE = re.compile(r"(\d+)\s*个\s*章节")
_ALL_CHAPTERS_RE = re.compile(r"各(个)?章节|每一章|所有章节|全部章节|每个章节")
_BOOK_CHAP_RE = re.compile(r"/book/(\d+)/[^/]+\.html?$", re.I)
_INDEX_PATH_RE = re.compile(
    r"^/(lists|other|search|all|novel|original|author|tag|rank)(/|$)",
    re.I,
)
_CHAPTER_MIN_CHARS = 200
_CONTENT_MIN_CHARS = 20
# 超过一批才落成一个临时文本。一批做完就丢掉，再读下一批，直到文本里的链接做完。
_TASK_BATCH = 20
_TASK_FILE_NAME = "目录任务临时文件.txt"


def _asked_chapter_count(text: str) -> int:
    """句子里点名的章数。没写数字、或写成 0，返回 0。不设上限。"""
    m = _ENTER_N_RE.search(text or "") or _N_CHAPTERS_RE.search(text or "")
    if not m:
        return 0
    n = int(m.group(1))
    return n if n > 0 else 0


def catalog_chapter_count(text: str) -> int:
    """0 = 不是「按目录进章节并保存原文」。

    正数 = 只要目录里前 N 个章节链接。-1 = 这一页上的章节链接都要。
    """
    t = text or ""
    if _REWRITE_RE.search(t):
        return 0
    if not SAVE_RE.search(t) or not wants_page_nav(t) or not find_url(t):
        return 0
    n = _asked_chapter_count(t)
    if n:
        return n
    if _ALL_CHAPTERS_RE.search(t):
        return -1
    return 0


def chapter_save_plan(text: str) -> int:
    """多章保存该由程序执行，而不是一章一轮问模型。

    0 = 不是这件任务。正数 = 只要前 N 个章节链接。-1 = 目录上的章节都要。
    借鉴 plan-and-execute：计划一旦能认出来，重复的打开和保存交给程序。
    """
    n = catalog_chapter_count(text)
    if n:
        return n
    t = text or ""
    if _REWRITE_RE.search(t) or is_convert_to_txt(t):
        return 0
    if not SAVE_RE.search(t) or not re.search(r"章节", t):
        return 0
    # 「工作目录」里的目录不算网页目录。没有网址、目录或「进入/分别」时，
    # 更像是让模型写一篇分章的文章。
    has_catalog = bool(
        find_url(t)
        or re.search(r"(?<!工作)目录", t)
        or re.search(r"进入|分别", t)
    )
    if not has_catalog:
        return 0
    count = _asked_chapter_count(t)
    if count:
        return count
    return -1


def pick_chapter_links(links, page_url: str = "") -> list:
    """从目录页链接里抽出同一本书的章节，保持页面上的顺序。

    导航、分类、排行不算章节。小说站的章节地址是 /book/书号/章节.html。
    """
    here = (page_url or "").split("#")[0].rstrip("/")
    books: dict = {}
    order = []
    for name, href in links or []:
        href = str(href or "").split("#")[0].rstrip("/")
        if not href.startswith(("http://", "https://")) or href == here:
            continue
        path = urlparse(href).path or ""
        m = _BOOK_CHAP_RE.search(path)
        if not m:
            continue
        if _INDEX_PATH_RE.search(path):
            continue
        bid = m.group(1)
        books.setdefault(bid, []).append((str(name or "").strip(), href))
        if bid not in order:
            order.append(bid)
    if not books:
        return []
    bid = max(order, key=lambda k: (len(books[k]), -order.index(k)))
    return [(name, href) for name, href in books[bid] if name or href]


def _transient_page_error(exc: Exception) -> bool:
    s = str(exc or "").lower()
    return any(k in s for k in (
        "connection", "reset", "timeout", "timed out", "10054", "aborted", "temporarily",
    ))


def _fetch_retry(url, should_stop=None, on_status=None, link_limit: int = 40, tries: int = 3):
    """目录站偶发断开连接。短暂失败再试，正文不存在这类错误不重试。"""
    last = None
    for i in range(max(1, int(tries or 1))):
        if should_stop and should_stop():
            raise PageError("已停止。")
        try:
            return fetch_page(
                url, should_stop=should_stop, on_status=on_status, link_limit=link_limit,
            )
        except PageError as e:
            last = e
            if i + 1 >= tries or not _transient_page_error(e):
                break
            log.info("ollama 打开网页重试 %s/%s %s", i + 1, tries, url)
            time.sleep(0.8)
    raise last


def _file_text(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read().replace("\r\n", "\n").replace("\r", "\n")


def catalog_finish_line(user_n: int, link_n: int, missing: list, on_disk: int, stopped: bool) -> str:
    """对照要求的章数和盘上够长的文件，给出完成或未完成。"""
    if stopped:
        return f"未完成：已停止，盘上 {max(0, int(on_disk))} 章。"
    parts = []
    if user_n > 0 and link_n < user_n:
        parts.append(f"目录里只有 {link_n} 个章节链接，要求 {user_n} 章")
    if missing:
        show = "、".join(str(x) for x in missing[:8])
        if len(missing) > 8:
            show += f" 等 {len(missing)} 章"
        parts.append("缺 " + show)
    if parts:
        return "未完成：" + "。".join(parts) + "。"
    total = link_n if user_n < 0 else min(user_n, link_n)
    return f"已完成，{total} 章都在。"


def _digest(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _content_index(root: str) -> dict:
    """文件名 → 正文摘要。读完即丢，不把各章正文留在内存里。"""
    found = {}
    try:
        names = os.listdir(root)
    except OSError:
        return found
    for name in names:
        ext = os.path.splitext(name)[1].lower()
        if ext not in (".txt", ".md", ".markdown") or name == _TASK_FILE_NAME:
            continue
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        try:
            found[name] = _digest(_file_text(path))
        except OSError:
            continue
    return found


def _match_saved(index: dict, body: str, expected_name: str, floor: int = _CHAPTER_MIN_CHARS) -> str:
    """相同正文已在盘上时返回那个文件名。比对的是摘要，不保留正文。"""
    if len((body or "").strip()) < floor:
        return ""
    digest = _digest(body)
    if index.get(expected_name) == digest:
        return expected_name
    for name, got in index.items():
        if name == expected_name:
            continue
        if got == digest:
            return name
    return ""


def _forget(page) -> None:
    """这一页已经用完。清掉引用，把这一章占用的内存还回去。"""
    if isinstance(page, dict):
        page.clear()
    gc.collect()


def _format_task(items, done: int = 0, failed=None) -> str:
    """一个临时文件：第一行是进度，后面每行一条链接，失败的标题写在末尾。"""
    lines = [f"进度\t{max(0, int(done))}"]
    for name, href in items or []:
        title = (name or "").replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()
        url = (href or "").replace("\t", "").replace("\r", "").replace("\n", "").strip()
        if url:
            lines.append(f"{title}\t{url}")
    for label in failed or []:
        text = str(label or "").replace("\t", " ").replace("\r", " ").replace("\n", "").strip()
        if text:
            lines.append(f"未完成\t{text}")
    return "\n".join(lines) + "\n"


def _parse_task_file(text: str) -> tuple:
    """返回 (链接, 已处理条数, 未完成标题)。"""
    items = []
    done = 0
    failed = []
    for line in (text or "").splitlines():
        if "\t" not in line:
            continue
        name, rest = line.split("\t", 1)
        name = name.strip()
        rest = rest.strip()
        if name == "进度":
            try:
                done = max(0, int(rest or "0"))
            except ValueError:
                done = 0
            continue
        if name == "未完成":
            if rest:
                failed.append(rest)
            continue
        if rest.startswith(("http://", "https://")):
            items.append((name, rest))
    return items, done, failed


def _read_task_file(path: str) -> tuple:
    try:
        return _parse_task_file(_file_text(path))
    except OSError:
        return [], 0, []


def _same_task(left, right) -> bool:
    if len(left or []) != len(right or []):
        return False
    for (a, au), (b, bu) in zip(left, right):
        if (a or "").strip() != (b or "").strip() or (au or "").strip() != (bu or "").strip():
            return False
    return True


def prepare_link_task(root: str, items) -> tuple:
    """长任务写成工作目录里的一个临时文本。返回 (文件, 从第几条继续, 是否沿用上次进度)。

    同一份链接还没做完时接着进度走。做完后文件留着，由用户自己删。
    """
    path = os.path.join(root, _TASK_FILE_NAME)
    old, done, _failed = _read_task_file(path)
    if _same_task(old, items) and 0 < done < len(items):
        return path, done, True
    write_text(path, _format_task(items, 0, []))
    return path, 0, False


def _load_task_batch(path: str, start: int, batch: int) -> tuple:
    """只把这一批读进内存。返回 (这一批, 文本里的总条数)。"""
    items, _done, _failed = _read_task_file(path)
    total = len(items)
    start = max(0, min(int(start or 0), total))
    chunk = items[start:start + max(1, int(batch))]
    del items
    return chunk, total


def _rewrite_task_progress(path: str, done: int, failed) -> None:
    items, _done, _failed = _read_task_file(path)
    write_text(path, _format_task(items, done, failed))
    del items


def _save_chapter_item(root, index, used, i, total, name, href, should_stop=None, on_status=None):
    """存一章。返回 ok / missing / stop，以及给记录用的一行短句。不保留正文。"""
    label = name or f"第{i}章"
    if should_stop and should_stop():
        return "stop", label, "已停止。"
    if on_status:
        on_status(f"正在保存第 {i}/{total} 章…")
    chapter = None
    body = ""
    got = ""
    try:
        try:
            chapter = _fetch_retry(href, should_stop=should_stop, on_status=on_status)
        except PageError as e:
            log.info("ollama 章节打开失败 %s：%s", href, e)
            return "missing", label, f"{label}：没有打开。"
        body = (chapter.get("text") or "").strip()
        floor = _CONTENT_MIN_CHARS if chapter.get("from_content") else _CHAPTER_MIN_CHARS
        title = chapter.get("title") or ""
        _forget(chapter)
        chapter = None
        if len(body) < floor:
            return "missing", label, f"{label}：正文太短，没有保存。"
        if not body.endswith("\n"):
            body += "\n"
        stem = safe_name(name or title or f"章节{i}")
        dest = os.path.join(root, stem + ".txt")
        if os.path.basename(dest) == _TASK_FILE_NAME:
            dest = unique_path(root, stem, ".txt")
        key = os.path.normcase(os.path.abspath(dest))
        if key in used:
            dest = unique_path(root, stem, ".txt")
            key = os.path.normcase(os.path.abspath(dest))
        used.add(key)
        expected = os.path.basename(dest)
        held = _match_saved(index, body, expected, floor)
        if held:
            return "ok", label, f"{label}：已在 {held}（{len(body.strip())} 字），没有再写。"
        written = write_text(dest, body)
        digest = _digest(body)
        try:
            got = _file_text(dest)
        except OSError:
            got = ""
        if got == body and len(got.strip()) >= floor:
            index[expected] = digest
            log.info("ollama 目录章节保存 %s chars=%d url=%s", dest, written, href)
            return "ok", label, f"已保存：{dest}（{written} 字）"
        return "missing", label, f"{label}：写入后读回不够 {floor} 字，没有算完成。"
    finally:
        body = ""
        got = ""
        _forget(chapter)


def _run_chapter_list(root, index, chosen, should_stop=None, on_status=None):
    """按顺序存完一份清单。返回 (记录行, 缺的标题, 盘上章数, 是否停止)。"""
    lines = []
    missing = []
    on_disk = 0
    used = set()
    total = len(chosen)
    for i, (name, href) in enumerate(chosen, 1):
        state, label, line = _save_chapter_item(
            root, index, used, i, total, name, href, should_stop, on_status,
        )
        if state == "stop":
            missing.extend((left or f"第{i + k}章") for k, (left, _href) in enumerate(chosen[i - 1:]))
            lines.append(line)
            return lines, missing, on_disk, True
        lines.append(line)
        if state == "ok":
            on_disk += 1
        else:
            missing.append(label)
    return lines, missing, on_disk, False


def _run_link_task(root, index, link_path, start, should_stop=None, on_status=None):
    """从临时文本里分批取。每做完一条就把进度写回这一个文件。"""
    lines = []
    _items, _done, failed = _read_task_file(link_path)
    del _items
    missing = list(failed) if start else []
    on_disk = 0
    used = set()
    done = max(0, int(start or 0))
    stopped = False
    while True:
        if should_stop and should_stop():
            stopped = True
            lines.append("已停止。")
            break
        batch, total = _load_task_batch(link_path, done, _TASK_BATCH)
        if not batch:
            break
        if on_status:
            on_status(f"正在处理第 {done + 1}–{done + len(batch)} 条，共 {total} 条…")
        for offset, (name, href) in enumerate(batch):
            state, label, line = _save_chapter_item(
                root, index, used, done + offset + 1, total, name, href,
                should_stop, on_status,
            )
            if state == "stop":
                missing.extend(
                    (left or f"第{done + offset + 1 + k}章")
                    for k, (left, _href) in enumerate(batch[offset:])
                )
                lines.append(line)
                stopped = True
                break
            lines.append(line)
            if state == "ok":
                on_disk += 1
            else:
                missing.append(label)
            done += 1
            _rewrite_task_progress(link_path, done, [x for x in missing if x])
        batch = []
        _forget(None)
        if stopped:
            break
    return lines, missing, on_disk, stopped, done


def save_catalog_chapters(article_dir: str, text: str, should_stop=None, on_status=None):
    """按目录链接把章节原文写入工作目录。不经过模型，不删已有文件。

    目录上没有章节链接时返回 None，调用方再交给原来的对话。
    """
    n = chapter_save_plan(text)
    if not n:
        return None
    url = find_url(text)
    from utils.ollama_dirs import _root
    root = _root(article_dir)
    if on_status:
        on_status("正在打开目录…")
    # 先看目录：链接有多少收多少。0 表示不按条数截断。
    page = _fetch_retry(
        url, should_stop=should_stop, on_status=on_status, link_limit=0,
    )
    page_url = page.get("url") or url
    found = pick_chapter_links(page.get("links") or [], page_url)
    _forget(page)
    if not found:
        log.info("ollama 目录页没有章节链接 %s", url)
        return None
    want = len(found) if n < 0 else min(n, len(found))
    chosen = found[:want]
    link_n = len(found)
    index = _content_index(root)
    task_note = ""
    if len(chosen) > _TASK_BATCH:
        link_path, start, resumed = prepare_link_task(root, chosen)
        chosen = []
        found = []
        _forget(None)
        if on_status:
            on_status("链接已写成文本，开始分批保存…")
        lines, missing, on_disk, stopped, done = _run_link_task(
            root, index, link_path, start, should_stop, on_status,
        )
        lines = [ln for ln in lines if not ln.startswith("已保存")]
        task_note = f"任务记在 {link_path}，共 {want} 条，已处理 {done} 条。做完可以删掉这个临时文件。"
        if resumed:
            task_note = "接着上次的文本任务。" + task_note
        log.info("ollama 目录任务文本 %s 从 %d 到 %d", link_path, start, done)
    else:
        lines, missing, on_disk, stopped = _run_chapter_list(
            root, index, chosen, should_stop, on_status,
        )
    if n > 0:
        head = catalog_finish_line(n, link_n, missing, on_disk, stopped)
    else:
        head = catalog_finish_line(want, want, missing, on_disk, stopped)
    log.info(
        "ollama 目录核对 %s 链接=%d 盘上=%d 缺=%d",
        head, want, on_disk, len(missing),
    )
    out = [head]
    if task_note:
        out.append(task_note)
    out.extend(lines)
    return "\n".join(out)


# ── 抓网页 ────────────────────────────────────────────────
_NOISE_TAGS = ("script", "style", "noscript", "iframe", "nav", "header", "footer", "aside",
               "form", "button", "select", "svg", "template")
_NOISE_RE = re.compile(
    r"(^|[-_\s])(nav|navbar|menu|header|footer|foot|top|bottom|sidebar|side|aside|ad|ads|advert|"
    r"banner|gg|tuijian|recommend|related|relate|hot|comment|comments|share|social|breadcrumb|crumb|"
    r"pager|pagination|page_?nav|toolbar|tool|login|search|copyright|friend|links|tags)($|[-_\s\d])",
    re.I,
)
_CONTENT_IDS = re.compile(
    r"^(content|chaptercontent|chapter_?content|booktxt|booktext|htmlcontent|txtcontent|txt|nr1|nr|"
    r"readcontent|read[-_]?content|article|article_?content|j_readcontent|contentbox|bookcontent|"
    r"novelcontent|text|main_?text|post_?content|entry_?content|acontent|chaptertxt)$",
    re.I,
)
_LINE_NOISE = re.compile(
    r"^(上一章|下一章|上一页|下一页|返回目录|目录|加入书签|投推荐票|章节报错|本章未完.*|"
    r".*(点击下一页|继续阅读|请记住本站|最新章节|手机阅读|天才一秒记住|笔趣阁|免费阅读|无弹窗).*)$"
)
_NEXT_PAGE_RE = re.compile(r"^\s*(下一页|下页|下一頁|next\s*page|›|»)\s*$", re.I)


class PageError(Exception):
    pass


def _get_html(url: str, timeout: float = 15.0):
    r = requests.get(url, headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"},
                     timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    ctype = (r.headers.get("Content-Type") or "").lower()
    if ctype and not any(k in ctype for k in ("text/", "html", "xml")):
        raise PageError(f"这个地址不是网页（{ctype.split(';')[0]}）")
    raw = r.content[:5_000_000]
    enc = None
    m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", raw[:4000], re.I)
    if m:
        enc = m.group(1).decode("ascii", "ignore")
    if not enc and "charset=" in ctype:
        enc = ctype.split("charset=")[-1].split(";")[0].strip()
    for e in (enc, "utf-8", "gb18030"):
        if not e:
            continue
        try:
            return raw.decode("gb18030" if e.lower() in ("gbk", "gb2312") else e), r.url
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace"), r.url


def _attr_text(el) -> str:
    cls = el.get("class") or []
    if isinstance(cls, str):
        cls = [cls]
    return " ".join([str(el.get("id") or "")] + [str(c) for c in cls])


def _block_text(el) -> str:
    """把一个元素变成纯文本：<br>/<p>/<div> 换行，段落之间空一行。"""
    from bs4 import NavigableString, Tag
    out = []

    def walk(node):
        for ch in node.children:
            if isinstance(ch, NavigableString):
                if type(ch).__name__ in ("Comment", "Doctype", "Declaration", "ProcessingInstruction"):
                    continue
                out.append(str(ch))
            elif isinstance(ch, Tag):
                name = ch.name.lower()
                if name == "br":
                    out.append("\n")
                    continue
                block = name in ("p", "div", "section", "article", "li", "h1", "h2", "h3", "h4",
                                 "h5", "h6", "blockquote", "pre", "tr", "dd", "dt")
                if block:
                    out.append("\n")
                walk(ch)
                if block:
                    out.append("\n")
    walk(el)
    text = "".join(out).replace("\r", "").replace("\xa0", " ").replace("\u3000", "  ")
    lines = []
    for ln in text.split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        if _LINE_NOISE.match(ln) and len(ln) < 40:
            continue
        lines.append(ln)
    return "\n\n".join(lines)


def _is_content_node(el) -> bool:
    """正文容器。id 或某个 class 对上即可，旁边再挂广告类名也不算广告。"""
    if _CONTENT_IDS.match(str(el.get("id") or "")):
        return True
    classes = el.get("class") or []
    if isinstance(classes, str):
        classes = classes.split()
    return any(_CONTENT_IDS.match(str(c)) for c in classes)


def _clean_soup(soup):
    for t in soup(list(_NOISE_TAGS)):
        t.decompose()
    for el in list(soup.find_all(True)):
        if el.attrs is None or el.name in ("html", "body"):
            continue
        a = _attr_text(el)
        if a.strip() and _NOISE_RE.search(a) and not _is_content_node(el):
            # 只删「链接多、正文少」的块，避免误删正文
            txt = el.get_text(" ", strip=True)
            link = sum(len(x.get_text(" ", strip=True)) for x in el.find_all("a"))
            if len(txt) < 200 or link > len(txt) * 0.3:
                el.decompose()


def _best_block(soup):
    """先认小说站常见的正文容器 id/class；认不出就挑「非链接文字最多」的块。"""
    for el in soup.find_all(True):
        ident = str(el.get("id") or "")
        if _CONTENT_IDS.match(ident):
            if len(el.get_text("", strip=True)) >= 50:
                return el
    for el in soup.find_all(True):
        for c in (el.get("class") or []):
            if _CONTENT_IDS.match(str(c)) and len(el.get_text("", strip=True)) >= 50:
                return el
    best, score = soup.body or soup, -1
    for el in soup.find_all(["div", "article", "section", "main", "td"]):
        txt = el.get_text("", strip=True)
        if len(txt) < 100:
            continue
        link = sum(len(a.get_text("", strip=True)) for a in el.find_all("a"))
        own = len(txt) - link
        # 直接子块越多越像外层容器，略微扣分，偏向最贴近正文的那一层
        s = own - 0.02 * len(txt) * len(el.find_all(["div", "section"], recursive=False))
        if s > score:
            best, score = el, s
    return best


_SKIP_LINK = re.compile(
    r"^(首页|主页|登录|註冊|注册|充值|排行|手机版|客户端|加入书架|书架|"
    r"上一章|下一章|上一页|下一页|返回|目录|最新章节|我的|退出|"
    r"home|login|sign\s*up|register)$",
    re.I,
)


def _page_links(soup, base_url: str, limit: int = 40) -> list:
    """从原始页面抽出链接（在清洗正文之前）。目录页主要信息往往在链接上。"""
    out = []
    seen = set()
    here = urlparse(base_url)
    here_key = (base_url or "").split("#")[0].rstrip("/")
    for a in soup.find_all("a", href=True):
        raw = (a.get("href") or "").strip()
        if not raw or raw.startswith(("javascript:", "mailto:", "tel:")):
            continue
        href = urljoin(base_url, raw).split("#")[0].rstrip("/")
        if not href.startswith(("http://", "https://")):
            continue
        if href == here_key:
            continue
        text = a.get_text(" ", strip=True)
        if not text or _SKIP_LINK.match(text):
            continue
        # 普通链接过长多半是误包的整段文字。章节标题经常超过 80 字，不能按这个丢掉。
        path = urlparse(href).path or ""
        chapter = bool(_BOOK_CHAP_RE.search(path)) and not _INDEX_PATH_RE.search(path)
        if len(text) > (200 if chapter else 80):
            continue
        if href in seen:
            continue
        # 优先同站，外链排后面
        other = urlparse(href)
        if here.netloc and other.netloc and other.netloc != here.netloc:
            continue
        seen.add(href)
        out.append((text, href))
        if limit > 0 and len(out) >= limit:
            break
    return out


def _title_of(soup) -> str:
    for tag in ("h1", "h2"):
        h = soup.find(tag)
        if h and h.get_text(strip=True):
            return h.get_text(" ", strip=True)[:80]
    t = soup.title.get_text(" ", strip=True) if soup.title else ""
    return re.split(r"\s*[-_|｜–—]\s*", t)[0][:80] if t else ""


def _same_chapter(cur: str, nxt: str) -> bool:
    """「下一页」是否还在同一章：同站同目录，文件名是当前页加 _2 / -2 之类的后缀。"""
    a, b = urlparse(cur), urlparse(nxt)
    if a.netloc != b.netloc:
        return False
    da, fa = os.path.split(a.path)
    db, fb = os.path.split(b.path)
    if da != db:
        return False
    sa = re.sub(r"([_-]\d{1,3})?\.\w+$", "", fa)
    sb = re.sub(r"([_-]\d{1,3})?\.\w+$", "", fb)
    return bool(sa) and sa == sb


def fetch_page(url: str, should_stop=None, on_status=None, max_pages: int = 30,
               link_limit: int = 40) -> dict:
    """抓整页正文。返回 {"title", "text", "pages", "url", "links"}；失败抛 PageError。"""
    from bs4 import BeautifulSoup
    seen, parts, title, pages, links = set(), [], "", 0, []
    from_content = False
    cur = url
    while cur and cur not in seen and pages < max_pages:
        if should_stop and should_stop():
            break
        seen.add(cur)
        if on_status:
            on_status("正在打开网页…" if not pages else f"正在打开网页（同一章第 {pages + 1} 页）…")
        try:
            html, final = _get_html(cur)
        except PageError:
            raise
        except Exception as e:
            if pages:
                break
            raise PageError(f"打开网页失败：{e}")
        soup = BeautifulSoup(html, "html.parser")
        if not title:
            title = _title_of(soup)
        batch = _page_links(soup, final, limit=link_limit if link_limit > 0 else 0)
        if link_limit > 0:
            if not links:
                links = batch
        else:
            # 目录任务：后面的分页也收下，不在第一页截断。
            have = {href for _name, href in links}
            for link_name, href in batch:
                if href in have:
                    continue
                have.add(href)
                links.append((link_name, href))
        nxt = ""
        for a in soup.find_all("a", href=True):
            if _NEXT_PAGE_RE.match(a.get_text(" ", strip=True) or ""):
                cand = urljoin(final, a["href"])
                if _same_chapter(final, cand):
                    nxt = cand
                break
        _clean_soup(soup)
        block = _best_block(soup)
        from_content = from_content or _is_content_node(block)
        for h in block.find_all(["h1"]):
            if h.get_text(" ", strip=True)[:80] == title:
                h.decompose()
        text = _block_text(block)
        if text:
            parts.append(text)
        pages += 1
        cur = nxt
    text = "\n\n".join(parts).strip()
    if not text and not links:
        raise PageError("页面里没有找到正文。")
    if not text:
        text = title or "（这一页主要是链接目录）"
    log.info(
        "ollama 打开网页 %s 标题=%s 页数=%d 字数=%d 链接=%d",
        url, title, pages, len(text), len(links),
    )
    return {
        "title": title, "text": text + "\n", "pages": pages, "url": url,
        "links": links, "from_content": from_content,
    }


# ── 文件名 / 工作文件 ────────────────────────────────────────
def safe_name(title: str, fallback: str = "网页正文") -> str:
    t = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", title or "").strip(" .")
    t = re.sub(r"\s{2,}", " ", t)[:60].strip(" .")
    if not t or t.upper() in ("CON", "PRN", "AUX", "NUL") or re.fullmatch(r"(COM|LPT)\d", t.upper()):
        t = fallback
    return t


def unique_path(folder: str, stem: str, ext: str = ".txt") -> str:
    p, i = os.path.join(folder, stem + ext), 2
    while os.path.exists(p):
        p = os.path.join(folder, f"{stem}({i}){ext}")
        i += 1
    return p


def work_dir() -> str:
    d = os.path.join(tempfile.gettempdir(), "DesktopAssistant_ollama")
    os.makedirs(d, exist_ok=True)
    return d


def write_text(path: str, text: str) -> int:
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)
    with open(path, encoding="utf-8") as f:
        return len(f.read())


def stash_source(stem: str, text: str) -> str:
    """把网页/粘贴的长文先存成一个工作文件，交给分段驱动当原文。"""
    path = unique_path(work_dir(), safe_name(stem) + time.strftime("_%Y%m%d%H%M%S"))
    write_text(path, text)
    return path