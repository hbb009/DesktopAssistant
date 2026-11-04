# utils/ollama_auto.py
# ---------------------------------------------------------------------------
# 让程序自己做决定，不让用户去调参数：
#   1. 上下文长度：按模型能力（/api/show）、已加载的长度（/api/ps）和这一轮任务的大小自动选。
#   2. 网页：用户消息里有网址时，程序自己把整页正文抓下来（去掉导航/广告/脚本/相关推荐，
#      小说站同一章的「下一页」会接着抓），写成干净的文本。
#   3. 保存原文类请求直接落盘，不经过模型；改写/翻译/总结等交给分段驱动。
# 纯函数，不碰 Qt。
# ---------------------------------------------------------------------------
from __future__ import annotations

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
AUTO_CEILING = 32768     # 默认的「省内存」上限；模型已按更大长度加载时以已加载的为准
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
    for m in d.get("models") or []:
        if model in (m.get("name"), m.get("model")):
            try:
                return int(m.get("context_length") or 0)
            except Exception:
                return 0
    return 0


def clear_cache() -> None:
    with _LOCK:
        _INFO_CACHE.clear()


def ceiling(max_ctx: int, loaded: int) -> int:
    """这一次最多用到多大：默认 32768；模型已按更大长度加载就用已加载的；不超过模型上限。"""
    top = max(AUTO_CEILING, loaded or 0)
    if max_ctx:
        top = min(top, max_ctx)
    return max(min(AUTO_MIN, max_ctx or AUTO_MIN), top)


def choose_ctx(need_tokens: int, max_ctx: int = 0, loaded: int = 0, floor: int = 0) -> int:
    """按需要的 token 数选上下文：留 20% 余量，取 2048 的倍数，最少 8192，
    不超过上限；不低于已加载的长度（避免 Ollama 反复重新加载），也不低于 floor。"""
    want = int(max(0, need_tokens) * 1.2) + 1024
    want = ((want + AUTO_STEP - 1) // AUTO_STEP) * AUTO_STEP
    want = max(AUTO_MIN, want, int(floor or 0))
    top = ceiling(max_ctx, loaded)
    n = min(want, top)
    if loaded and (not max_ctx or loaded <= max_ctx):
        n = max(n, loaded)
    return n


def escalate(cur: int, max_ctx: int = 0, loaded: int = 0) -> int:
    """放大一档（翻倍，到上限为止）。已经到顶返回原值。"""
    return min(max(cur * 2, AUTO_MIN), ceiling(max_ctx, loaded)) if cur else AUTO_MIN


# ── 识别请求 ───────────────────────────────────────────────
URL_RE = re.compile(r"https?://[^\s<>\"'，。；！？、）】」』]+|www\.[^\s<>\"'，。；！？、）】」』]+", re.I)
SAVE_RE = re.compile(r"保存|存到|存进|存下|存入|存成|存为|下载|另存|落盘|抓取|扒下|扒取|写入工作目录|放到工作目录")
_FULL_RE = re.compile(r"完整|全部|全文|原文|整章|整篇|原样")


def find_url(text: str) -> str:
    m = URL_RE.search(text or "")
    if not m:
        return ""
    u = m.group(0).rstrip(".,;:!?)]}")
    return u if u.lower().startswith("http") else "https://" + u


def is_save_original(text: str, task_re) -> bool:
    """「保存/下载/存到工作目录」且没有改写/翻译/总结之类的要求。"""
    t = text or ""
    return bool(SAVE_RE.search(t)) and not task_re.search(t)


def is_save_like(text: str) -> bool:
    return bool(SAVE_RE.search(text or ""))


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
    r"readcontent|read_?content|article|article_?content|j_readcontent|contentbox|bookcontent|"
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


def _clean_soup(soup):
    for t in soup(list(_NOISE_TAGS)):
        t.decompose()
    for el in list(soup.find_all(True)):
        if el.attrs is None or el.name in ("html", "body"):
            continue
        a = _attr_text(el)
        if a.strip() and _NOISE_RE.search(a) and not _CONTENT_IDS.match(str(el.get("id") or "")):
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


def fetch_page(url: str, should_stop=None, on_status=None, max_pages: int = 30) -> dict:
    """抓整页正文。返回 {"title", "text", "pages", "url"}；失败抛 PageError。"""
    from bs4 import BeautifulSoup
    seen, parts, title, pages = set(), [], "", 0
    cur = url
    while cur and cur not in seen and pages < max_pages:
        if should_stop and should_stop():
            break
        seen.add(cur)
        if on_status:
            on_status("正在抓取网页…" if not pages else f"正在抓取网页（同一章第 {pages + 1} 页）…")
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
        nxt = ""
        for a in soup.find_all("a", href=True):
            if _NEXT_PAGE_RE.match(a.get_text(" ", strip=True) or ""):
                cand = urljoin(final, a["href"])
                if _same_chapter(final, cand):
                    nxt = cand
                break
        _clean_soup(soup)
        block = _best_block(soup)
        for h in block.find_all(["h1"]):
            if h.get_text(" ", strip=True)[:80] == title:
                h.decompose()
        text = _block_text(block)
        if text:
            parts.append(text)
        pages += 1
        cur = nxt
    text = "\n\n".join(parts).strip()
    if not text:
        raise PageError("页面里没有找到正文。")
    log.info("ollama 抓取网页 %s 标题=%s 页数=%d 字数=%d", url, title, pages, len(text))
    return {"title": title, "text": text + "\n", "pages": pages, "url": url}


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
