# utils/missav.py
# ---------------------------------------------------------------------------
# MissAV 解析（「其它视频」子页用）：只认视频页
#   https://missav.live/cn/fpre-210
#   https://missav.live/fpre-210
# 可选两位语言前缀。番号里要有一段「-数字」，后面可以再接 -uncensored-leak 这类后缀。
# 栏目页（actresses、genres、uncensored-leak、today-hot 等）不是视频页。
#
# 播放地址写在页面里一段压缩脚本中，解压后是：
#   source     = https://surrit.com/<id>/playlist.m3u8     主清单
#   source1280 = https://surrit.com/<id>/1080p/video.m3u8  具体画质
#   source842  = https://surrit.com/<id>/720p/video.m3u8
# 分片地址以 .jpeg 结尾，内容是 MPEG-TS。现有 HLS 下载按字节拼接再封装。
# 本模块不依赖 Qt。
# ---------------------------------------------------------------------------

from __future__ import annotations

import html as _html
import re
from typing import Callable, List, Optional, Tuple
from urllib.parse import urlparse

from utils.xvideos import parse_hls_master

MA_REFERER = "https://missav.live/"
MA_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# 视频页。语言前缀可有可无。番号中含「-数字」，后缀如 -uncensored-leak 仍算同一页。
_MA_URL_RE = re.compile(
    r"https?://(?:[\w-]+\.)?missav\.live/(?:[a-z]{2}/)?"
    r"[a-z0-9]+(?:-[a-z0-9]+)*-\d+(?:-[a-z0-9]+)*"
    r"(?=[/?#&;\"'<\s\u4e00-\u9fff，。！？、；：]|$)",
    re.I,
)
_PACK_RE = re.compile(
    r"\}\(\s*'((?:\\.|[^'\\])*)'\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*'((?:\\.|[^'\\])*)'\.split\(\s*'\|'\s*\)",
    re.S,
)
_ASSIGN_RE = re.compile(r"\b(source\w*)='(https?://[^']+)'")
_HEIGHT_RE = re.compile(r"/(\d{3,4})p/", re.I)
_META_RE = re.compile(
    r'<meta\s+(?:property|name)=["\']([^"\']+)["\']\s+content="([^"]*)"',
    re.I,
)
_META_RE2 = re.compile(
    r'<meta\s+content="([^"]*)"\s+(?:property|name)=["\']([^"\']+)["\']',
    re.I,
)


class MissAVError(Exception):
    """解析失败（不是视频页 / 页面没有播放地址 / 站点拒绝）。"""


def extract_missav_url(text: str) -> str:
    """从文本里取第一条 MissAV 视频页链接；栏目页、首页返回空串。"""
    m = _MA_URL_RE.search(text or "")
    if not m:
        return ""
    return _html.unescape(m.group(0)).rstrip(").,;]}\"'")


def looks_like_missav(text: str) -> bool:
    """是否含 MissAV 视频页链接。"""
    return bool(extract_missav_url(text))


def _meta(body: str, prop: str) -> str:
    want = (prop or "").lower()
    for mm in _META_RE.finditer(body or ""):
        if mm.group(1).lower() == want:
            return _html.unescape(mm.group(2)).strip()
    for mm in _META_RE2.finditer(body or ""):
        if mm.group(2).lower() == want:
            return _html.unescape(mm.group(1)).strip()
    return ""


def _js_unescape(s: str) -> str:
    """解开脚本字符串里的 \\ 和 \\'。"""
    return (s or "").replace("\\\\", "\\").replace("\\'", "'").replace('\\"', '"')


def _to_base(n: int, base: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if n <= 0:
        return "0"
    out = []
    while n:
        n, rem = divmod(n, base)
        out.append(digits[rem])
    return "".join(reversed(out))


def _unpack_player(body: str) -> str:
    """解压带 m3u8 词典的播放器脚本。找不到返回空串。"""
    for m in _PACK_RE.finditer(body or ""):
        table = _js_unescape(m.group(4))
        if "m3u8" not in table or "surrit" not in table:
            continue
        try:
            radix = int(m.group(2))
            count = int(m.group(3))
        except (TypeError, ValueError):
            continue
        if radix < 2 or radix > 36 or count < 1 or count > 5000:
            continue
        words = table.split("|")
        out = _js_unescape(m.group(1))
        for i in range(count - 1, -1, -1):
            word = words[i] if i < len(words) else ""
            if not word:
                continue
            token = _to_base(i, radix)
            out = re.sub(
                r"\b" + re.escape(token) + r"\b",
                lambda _mm, w=word: w,
                out,
            )
        return out
    return ""


def _specs_from_script(script: str) -> List[dict]:
    found = []
    seen = set()
    for _name, url in _ASSIGN_RE.findall(script or ""):
        if ".m3u8" not in url.lower():
            continue
        path = urlparse(url).path.lower()
        if path.endswith("/playlist.m3u8"):
            continue
        hm = _HEIGHT_RE.search(path)
        try:
            height = int(hm.group(1)) if hm else 0
        except (TypeError, ValueError):
            height = 0
        key = url.split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        found.append({
            "label": f"{height}p · HLS" if height else "HLS",
            "url": url,
            "ext": ".mp4",
            "hls": True,
            "height": height,
        })
    found.sort(key=lambda d: int(d.get("height") or 0), reverse=True)
    return found


def _master_url(script: str) -> str:
    for _name, url in _ASSIGN_RE.findall(script or ""):
        if urlparse(url).path.lower().endswith("/playlist.m3u8"):
            return url
    return ""


def parse_missav_page(
    body: str, page_url: str, fetch_text: Optional[Callable[[str, str], str]] = None,
) -> Tuple[dict, List[dict]]:
    """解析视频页 HTML。返回 (info, specs)，specs 第一项是最高画质。

    fetch_text(url, referer) 用来拉主清单；为空时只用脚本里直接写出的画质。
    """
    script = _unpack_player(body or "")
    specs: List[dict] = []
    hls_err = ""
    master = _master_url(script)
    if master and fetch_text is not None:
        try:
            variants = parse_hls_master(fetch_text(master, page_url), master)
        except Exception as e:
            hls_err = str(e) or repr(e)
            variants = []
        for v in variants:
            h = int(v.get("height") or 0)
            url = v.get("url") or ""
            if not url:
                continue
            bits = [f"{h}p" if h else (v.get("name") or "HLS")]
            if v.get("res"):
                bits.append(v["res"])
            bits.append("HLS")
            specs.append({
                "label": " · ".join(bits),
                "url": url,
                "ext": ".mp4",
                "hls": True,
                "height": h,
            })
    if not specs:
        specs = _specs_from_script(script)
    if not specs:
        raise MissAVError("页面里没有播放地址（视频可能已删除，或页面结构已变）")
    dur_raw = _meta(body, "og:video:duration")
    try:
        duration = int(float(dur_raw)) if dur_raw else 0
    except (TypeError, ValueError):
        duration = 0
    info = {
        "title": _meta(body, "og:title") or "MissAV",
        "author": _meta(body, "author") or "MissAV",
        "id": "",
        "cover": _meta(body, "og:image"),
        "duration": duration,
        "platform": "missav",
        "referer": page_url,
        "page_url": page_url,
        "hls_error": hls_err,
    }
    return info, specs


def fetch_text(url: str, referer: str = MA_REFERER, timeout: float = 30, session=None) -> str:
    """GET 文本。404 给出明确提示。"""
    if session is None:
        import requests
        session = requests
    r = session.get(
        url,
        headers={
            "User-Agent": MA_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": referer or MA_REFERER,
        },
        timeout=timeout,
    )
    if r.status_code == 404:
        raise MissAVError("视频不存在或已被删除（HTTP 404）")
    if r.status_code in (401, 403, 451):
        raise MissAVError(f"站点拒绝访问（HTTP {r.status_code}），可能需要代理或被地区限制")
    r.raise_for_status()
    r.encoding = r.encoding or "utf-8"
    return r.text or ""


def parse_missav(url_text: str, session=None, log_cb=None) -> Tuple[dict, List[dict]]:
    """从链接文本解析：拉页面 → 解压播放地址。"""
    url = extract_missav_url(url_text)
    if not url:
        raise MissAVError(
            "不是 MissAV 视频页链接（需要 missav.live/…/番号，例如 …/cn/fpre-210 或 …/cn/mism-435-uncensored-leak）"
        )
    if log_cb:
        log_cb(f"识别 MissAV 链接：{url}")
        log_cb("拉取视频页 HTML…")
    body = fetch_text(url, MA_REFERER, session=session)
    info, specs = parse_missav_page(
        body, url, lambda u, ref: fetch_text(u, ref, timeout=20, session=session),
    )
    if log_cb and info.get("hls_error"):
        log_cb(f"HLS 主清单读取失败，只提供页面里直接写出的画质：{info['hls_error']}", "warn")
    return info, specs
