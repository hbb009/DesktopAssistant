# utils/xcom.py
# ---------------------------------------------------------------------------
# x.com / twitter.com 解析（「其它视频」子页用）：只认帖子页
#   https://x.com/PJaccetturo/status/2121249226575511854/video/1
#   https://twitter.com/用户/status/数字
# /video/N 取第 N 个视频（从 1 起）。没有这段时，帖子里只有一个视频就用它。
#
# 公开 syndication 接口给出各档 MP4 直链（video.twimg.com），不需要登录。
# 本模块不依赖 Qt。
# ---------------------------------------------------------------------------

from __future__ import annotations

import collections
import html as _html
import math
import re
from typing import List, Optional, Tuple

X_REFERER = "https://x.com/"
_SYNDICATION = "https://cdn.syndication.twimg.com/tweet-result"

# 帖子页。主机只认 x.com / twitter.com（含 www、mobile）。
_STATUS_RE = re.compile(
    r"https?://(?:(?:www|mobile|m)\.)?(?:x\.com|twitter\.com)/"
    r"(?:(?:i/web|[^/\s?#]+)/status|statuses)/(\d+)"
    r"(?:/(?:video|photo)/(\d+))?",
    re.I,
)
_DIM_RE = re.compile(r"/(\d+)x(\d+)/")
_TCO_RE = re.compile(r"https?://t\.co/\w+")
_ALPHABET = b"0123456789abcdefghijklmnopqrstuvwxyz.-"


class XComError(Exception):
    """解析失败（不是帖子页 / 没有视频 / 接口拒绝）。"""


def extract_xcom_url(text: str) -> str:
    """从文本里取第一条 x.com / twitter.com 帖子链接。主页、个人页返回空串。"""
    m = _STATUS_RE.search(text or "")
    if not m:
        return ""
    return _html.unescape(m.group(0)).rstrip(").,;]}\"'")


def looks_like_xcom(text: str) -> bool:
    """是否含 x.com / twitter.com 帖子链接。"""
    return bool(extract_xcom_url(text))


def _status_parts(url: str) -> Tuple[str, int]:
    m = _STATUS_RE.search(url or "")
    if not m:
        return "", 0
    try:
        index = int(m.group(2) or 0)
    except (TypeError, ValueError):
        index = 0
    return m.group(1), index


def _js_number_to_string(val: float, radix: int = 36) -> str:
    """对齐 JavaScript Number.toString(radix)。syndication token 要用。"""
    if math.isnan(val):
        return "NaN"
    if val == 0:
        return "0"
    if math.isinf(val):
        return "-Infinity" if val < 0 else "Infinity"
    result = collections.deque()
    sign = val < 0
    val = abs(val)
    fraction, integer = math.modf(val)
    delta = max(math.nextafter(0.0, math.inf), math.ulp(val) / 2)
    if fraction >= delta:
        result.append(-2)  # '.'
    while fraction >= delta:
        delta *= radix
        fraction, digit = math.modf(fraction * radix)
        result.append(int(digit))
        needs_rounding = fraction > 0.5 or (fraction == 0.5 and int(digit) & 1)
        if needs_rounding and fraction + delta > 1:
            for index in reversed(range(1, len(result))):
                if result[index] + 1 < radix:
                    result[index] += 1
                    break
                result.pop()
            else:
                integer += 1
            break
    integer, digit = divmod(int(integer), radix)
    result.appendleft(digit)
    while integer > 0:
        integer, digit = divmod(integer, radix)
        result.appendleft(digit)
    if sign:
        result.appendleft(-1)  # '-'
    return bytes(_ALPHABET[digit] for digit in result).decode("ascii")


def syndication_token(tweet_id: str) -> str:
    """((id / 1e15) * π).toString(36)，再去掉 0 和小数点。"""
    raw = _js_number_to_string((int(tweet_id) / 1e15) * math.pi, 36)
    return raw.translate(str.maketrans("", "", "0."))


def _session(session):
    if session is not None:
        return session
    import requests
    return requests


def fetch_tweet(tweet_id: str, session=None, timeout: float = 30) -> dict:
    """拉公开 syndication JSON。404 是帖子不存在。"""
    http = _session(session)
    r = http.get(
        _SYNDICATION,
        params={"id": str(tweet_id), "token": syndication_token(str(tweet_id))},
        headers={"User-Agent": "Googlebot", "Accept": "application/json"},
        timeout=timeout,
    )
    if r.status_code == 404:
        raise XComError("帖子不存在或已被删除（HTTP 404）")
    if r.status_code in (401, 403):
        raise XComError(f"x.com 拒绝访问（HTTP {r.status_code}）")
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict) or not data:
        raise XComError("x.com 没有返回帖子数据")
    return data


def _video_items(data: dict) -> List[dict]:
    out = []
    for item in data.get("mediaDetails") or []:
        if not isinstance(item, dict):
            continue
        if (item.get("type") or "").lower() not in ("video", "animated_gif"):
            continue
        out.append(item)
    if out:
        return out
    video = data.get("video")
    if isinstance(video, dict) and (video.get("variants") or video.get("poster")):
        return [video]
    return out


def _variants_of(item: dict) -> list:
    info = item.get("video_info") if isinstance(item.get("video_info"), dict) else {}
    variants = info.get("variants") or item.get("variants") or []
    return variants if isinstance(variants, list) else []


def _specs_from_variants(variants: list) -> List[dict]:
    specs = []
    seen = set()
    hls_url = ""
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        url = (variant.get("url") or variant.get("src") or "").strip()
        if not url:
            continue
        ctype = (variant.get("content_type") or variant.get("type") or "").lower()
        if "mpegurl" in ctype or url.lower().split("?")[0].endswith(".m3u8"):
            hls_url = hls_url or url
            continue
        if "mp4" not in ctype and not url.lower().split("?")[0].endswith(".mp4"):
            continue
        key = url.split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        try:
            bitrate = int(variant.get("bitrate") or variant.get("bit_rate") or 0)
        except (TypeError, ValueError):
            bitrate = 0
        dims = _DIM_RE.search(url)
        width = int(dims.group(1)) if dims else 0
        height = int(dims.group(2)) if dims else 0
        specs.append({
            "url": url,
            "ext": ".mp4",
            "hls": False,
            "height": height,
            "width": width,
            "bitrate": bitrate,
        })
    specs.sort(key=lambda d: (int(d.get("height") or 0), int(d.get("bitrate") or 0)), reverse=True)
    if not specs and hls_url:
        specs.append({
            "url": hls_url,
            "ext": ".mp4",
            "hls": True,
            "height": 0,
            "width": 0,
            "bitrate": 0,
        })
    used = set()
    for spec in specs:
        if spec.get("hls"):
            base = "HLS"
        elif spec.get("height"):
            base = f"{int(spec['height'])}p"
        elif spec.get("bitrate"):
            base = f"{int(spec['bitrate']) // 1000}k"
        else:
            base = "MP4"
        label = base
        n = 2
        while label in used:
            if spec.get("bitrate") and n == 2:
                label = f"{base} {int(spec['bitrate']) // 1000}k"
            else:
                label = f"{base} {n}"
            n += 1
        used.add(label)
        kind = "HLS" if spec.get("hls") else "直链"
        spec["label"] = f"{label} · {kind}"
    return specs


def _clean_title(data: dict) -> str:
    text = _TCO_RE.sub("", data.get("text") or "")
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n-—")
    return text or "x.com"


def _pick_item(items: List[dict], index: int) -> dict:
    if index:
        if index < 1 or index > len(items):
            raise XComError(f"这条帖子没有第 {index} 个视频（共 {len(items)} 个）")
        return items[index - 1]
    if len(items) == 1:
        return items[0]
    raise XComError(
        f"这条帖子有 {len(items)} 个视频。请在链接末尾加上 /video/1 这类序号后再解析。"
    )


def parse_tweet(data: dict, page_url: str) -> Tuple[dict, List[dict]]:
    """syndication JSON → (info, specs)。specs 第一项是最高画质。"""
    tweet_id, index = _status_parts(page_url)
    items = _video_items(data)
    if not items:
        raise XComError("这条帖子里没有视频（可能是纯文字、图片，或需要登录才能看）")
    item = _pick_item(items, index)
    specs = _specs_from_variants(_variants_of(item))
    if not specs:
        raise XComError("帖子里有视频，但没有可下载的地址")
    user = data.get("user") if isinstance(data.get("user"), dict) else {}
    screen = (user.get("screen_name") or "").strip()
    author = (user.get("name") or "").strip() or screen or "x.com"
    info_block = item.get("video_info") if isinstance(item.get("video_info"), dict) else {}
    try:
        duration_ms = int(info_block.get("duration_millis") or item.get("durationMs") or 0)
    except (TypeError, ValueError):
        duration_ms = 0
    cover = (
        item.get("media_url_https")
        or item.get("poster")
        or ""
    )
    info = {
        "title": _clean_title(data),
        "author": author,
        "id": tweet_id,
        "screen_name": screen,
        "cover": cover,
        "duration": int(round(duration_ms / 1000.0)) if duration_ms else 0,
        "platform": "xcom",
        "referer": X_REFERER,
        "page_url": page_url,
    }
    return info, specs


def parse_xcom(url_text: str, session=None, log_cb=None) -> Tuple[dict, List[dict]]:
    """从链接文本解析：认帖子 → 拉 syndication → 各档 MP4。"""
    url = extract_xcom_url(url_text)
    if not url:
        raise XComError(
            "不是 x.com 帖子链接（需要 x.com/用户/status/数字，可选 /video/1）"
        )
    tweet_id, index = _status_parts(url)
    if log_cb:
        extra = f" · 第 {index} 个视频" if index else ""
        log_cb(f"识别 x.com 链接：{url}{extra}")
        log_cb("拉取帖子信息…")
    data = fetch_tweet(tweet_id, session=session)
    return parse_tweet(data, url)
