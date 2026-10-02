# utils/xvideos.py
# ---------------------------------------------------------------------------
# xvideos 解析（「其它视频」子页用）：只认视频页链接
#   https://www.xvideos.com/video.<id>/<slug>   或   https://www.xvideos.com/video<数字>/<slug>
# 页面 html5player.* 取标题 / 上传者 / 编号，og:* 取时长和封面（jpg）；
# setVideoHLS 指向 HLS 主清单，按画质高→低排列；MP4 高/低直链去重后排在 HLS 之后。
# 本模块不依赖 Qt，便于单测与命令行自检。
# ---------------------------------------------------------------------------

from __future__ import annotations

import html as _html
import re
from typing import Callable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

XV_REFERER = "https://www.xvideos.com/"
XV_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

_XV_URL_RE = re.compile(
    r"https?://(?:[\w-]+\.)?xvideos\d*\.com/(?:video\.[0-9a-z]+|video\d+)(?=[/?#]|$)"
    r"[^\s\"'<>\u4e00-\u9fff，。！？、；：]*",
    re.I,
)


class XVideosError(Exception):
    """解析失败（页面不存在 / 无播放器数据 / 无可下载地址）。"""


def extract_xvideos_url(text: str) -> str:
    """从文本里取第一条 xvideos 视频页链接；不是视频页（标签页、首页等）返回空串。"""
    m = _XV_URL_RE.search(text or "")
    if not m:
        return ""
    return m.group(0).rstrip(").,;]}\"'")


def looks_like_xvideos(text: str) -> bool:
    """是否含 xvideos 视频页链接。"""
    return bool(extract_xvideos_url(text))


def _js_arg(body: str, fn: str) -> str:
    m = re.search(
        r"html5player\.%s\(\s*(['\"])(.*?)\1\s*\)" % re.escape(fn), body or "", re.S
    )
    return _html.unescape(m.group(2)).strip() if m else ""


def _meta(body: str, prop: str) -> str:
    m = re.search(
        r'<meta\s+property="%s"\s+content="([^"]*)"' % re.escape(prop), body or ""
    )
    return _html.unescape(m.group(1)).strip() if m else ""


def parse_hls_master(text: str, base_url: str) -> List[dict]:
    """HLS 主清单 → [{height,res,name,bw,url}]，按高度（再按码率）从高到低。"""
    lines = [ln.strip() for ln in (text or "").splitlines()]
    out = []
    for i, ln in enumerate(lines):
        if not ln.startswith("#EXT-X-STREAM-INF:"):
            continue
        attrs = dict(re.findall(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', ln.split(":", 1)[1]))
        uri = next((x for x in lines[i + 1:] if x and not x.startswith("#")), "")
        if not uri:
            continue
        res = attrs.get("RESOLUTION", "").strip('"')
        name = attrs.get("NAME", "").strip('"')
        try:
            h = int(res.lower().split("x")[1]) if "x" in res.lower() else int(re.sub(r"\D", "", name) or 0)
        except ValueError:
            h = 0
        try:
            bw = int(re.sub(r"\D", "", attrs.get("BANDWIDTH", "")) or 0)
        except ValueError:
            bw = 0
        out.append({
            "height": h,
            "res": res.lower().replace("x", "×"),
            "name": name or (f"{h}p" if h else "HLS"),
            "bw": bw,
            "url": urljoin(base_url, uri),
        })
    out.sort(key=lambda d: (d["height"], d["bw"]), reverse=True)
    return out


def quality_key(label: str) -> str:
    """画质卡标签的首段（「1080p」「MP4 高」），用于令牌过期后重新解析找回同一档。"""
    return (label or "").split("·")[0].strip()


def parse_xvideos_page(
    body: str, page_url: str, fetch_text: Optional[Callable[[str, str], str]] = None
) -> Tuple[dict, List[dict]]:
    """解析视频页 HTML。

    返回 (info, specs)：specs=[{label,url,ext,hls,height}]，第一项为最高画质。
    fetch_text(url, referer) -> str 用来拉 HLS 主清单；为空则只给 MP4 直链。
    """
    body = body or ""
    has_player = ("setVideoHLS" in body) or ("setVideoUrlHigh" in body) or ("setVideoUrlLow" in body)
    if not has_player:
        raise XVideosError("页面里没有播放器数据（视频可能已删除、需要登录或被地区限制）")
    title = _js_arg(body, "setVideoTitle") or _meta(body, "og:title") or "xvideos"
    dur = _meta(body, "og:duration")
    info = {
        "title": title,
        "author": _js_arg(body, "setUploaderName") or "xvideos",
        "id": _js_arg(body, "setEncodedIdVideo"),
        # setThumbUrl169 常是 .avif（Qt 默认不解码），og:image 是 jpg
        "cover": _meta(body, "og:image") or _js_arg(body, "setThumbUrl"),
        "duration": int(dur) if dur.isdigit() else 0,
        "platform": "xvideos",
        "referer": page_url,
        "page_url": page_url,
    }
    specs: List[dict] = []
    hls = _js_arg(body, "setVideoHLS")
    hls_err = ""
    if hls and fetch_text is not None:
        try:
            for v in parse_hls_master(fetch_text(hls, page_url), hls):
                bits = [v["name"]] + ([v["res"]] if v["res"] else []) + ["HLS"]
                specs.append({
                    "label": " · ".join(bits),
                    "url": v["url"],
                    "ext": ".mp4",
                    "hls": True,
                    "height": v["height"],
                })
        except Exception as e:  # HLS 失败时仍给 MP4 直链
            hls_err = str(e) or repr(e)
    seen = set()
    for fn, lab in (("setVideoUrlHigh", "MP4 高"), ("setVideoUrlLow", "MP4 低")):
        u = _js_arg(body, fn)
        if not u:
            continue
        k = u.split("?")[0]
        if k in seen:
            continue
        seen.add(k)
        specs.append({"label": f"{lab} · 直链", "url": u, "ext": ".mp4", "hls": False, "height": 0})
    if not specs:
        raise XVideosError(
            "未找到可下载的视频地址" + (f"（HLS：{hls_err}）" if hls_err else "")
        )
    info["hls_error"] = hls_err
    return info, specs


def is_hls_url(url: str) -> bool:
    return urlparse(url or "").path.lower().endswith(".m3u8")


_MAP_URI_RE = re.compile(r'#EXT-X-MAP:[^\n]*?URI="([^"]+)"')


def hls_is_fmp4(playlist_text: str) -> bool:
    """清单是否 fMP4（分片为 .m4s，前面还有一段 EXT-X-MAP 初始化段）。

    xvideos 是 MPEG-TS（无 MAP），pornhub 是 fMP4：两者拼接方式不同，
    拼接方式见 hls_segments。
    """
    return "#EXT-X-MAP:" in (playlist_text or "")


def hls_init_segment(playlist_text: str, playlist_url: str) -> str:
    """fMP4 初始化段绝对地址；MPEG-TS（无 MAP）返回空串。"""
    m = _MAP_URI_RE.search(playlist_text or "")
    return urljoin(playlist_url, m.group(1).strip()) if m else ""


def hls_segments(playlist_text: str, playlist_url: str) -> List[str]:
    """媒体清单 → 分片绝对地址。加密分片不支持。

    fMP4（Pornhub）会把 EXT-X-MAP 初始化段排在最前：缺了它拼出来的 mp4 播不了。
    MPEG-TS（xvideos）没有 MAP，返回值与原来一致。
    """
    text = playlist_text or ""
    if "#EXT-X-STREAM-INF" in text:
        raise XVideosError("这是 HLS 主清单，不是具体画质的分片清单")
    for ln in text.splitlines():
        if ln.startswith("#EXT-X-KEY") and "METHOD=NONE" not in ln:
            raise XVideosError("HLS 分片已加密，暂不支持")
    segs = [
        urljoin(playlist_url, ln.strip())
        for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    if not segs:
        raise XVideosError("HLS 清单里没有分片")
    init = hls_init_segment(text, playlist_url)
    if init:
        segs.insert(0, init)
    return segs


def _requests():
    import requests  # 延迟导入：本模块可在无 requests 环境下做纯解析
    return requests


def fetch_text(url: str, referer: str = XV_REFERER, timeout: float = 30, session=None) -> str:
    """GET 文本（requests 默认读取系统代理）。404 给出明确提示。"""
    req = session or _requests()
    r = req.get(
        url,
        headers={
            "User-Agent": XV_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
            "Referer": referer or XV_REFERER,
        },
        timeout=timeout,
    )
    if r.status_code == 404:
        raise XVideosError("视频不存在或已被删除（HTTP 404）")
    if r.status_code in (401, 403, 451):
        raise XVideosError(f"站点拒绝访问（HTTP {r.status_code}），可能需要代理或被地区限制")
    r.raise_for_status()
    r.encoding = r.encoding or "utf-8"
    return r.text or ""


def parse_xvideos(url_text: str, session=None, log_cb=None) -> Tuple[dict, List[dict]]:
    """从链接文本解析：拉页面 → 解析 → 拉 HLS 主清单。"""
    url = extract_xvideos_url(url_text)
    if not url:
        raise XVideosError("不是 xvideos 视频页链接（需要 xvideos.com/video.xxx/…）")
    if log_cb:
        log_cb(f"识别 XVideos 链接：{url}")
        log_cb("拉取视频页 HTML…")
    body = fetch_text(url, XV_REFERER, session=session)
    info, specs = parse_xvideos_page(
        body, url, lambda u, ref: fetch_text(u, ref, timeout=20, session=session)
    )
    if log_cb and info.get("hls_error"):
        log_cb(f"HLS 清单读取失败，只提供 MP4 直链：{info['hls_error']}", "warn")
    return info, specs


def pick_same_quality(specs: List[dict], label: str) -> Optional[dict]:
    """重新解析后找回与原画质卡同一档；找不到返回 None。"""
    want = quality_key(label)
    for s in specs or []:
        if quality_key(s.get("label") or "") == want:
            return s
    return None
