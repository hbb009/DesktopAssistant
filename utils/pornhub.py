# utils/pornhub.py
# ---------------------------------------------------------------------------
# pornhub 解析（「其它视频」子页用）：只认视频页链接
#   https://<任意镜像>/view_video.php?viewkey=<key>     （embed/<key> 也认）
# 镜像很多（www / cn / ru …），全部由 PH_URL_RE 的子域通配覆盖。
#
# 页面取信息的两处来源：
#   · <script type="application/ld+json"> 的 VideoObject：name / author /
#     duration(ISO8601) / thumbnailUrl —— 标题、上传者、时长、封面都齐
#   · 页面内嵌 JSON 的 mediaDefinitions 数组：每档画质一条
#     {"format":"hls","height":1080,"videoUrl":"…/1080P_…mp4/master.m3u8?h=…"}
#     videoUrl 每次访问页面重新签发，约 2 小时后失效。
#
# 注意与 xvideos 的两点差异：
#   1. 末尾还有一条 format="mp4" 的 get_media 跳转地址，只能拿到 240P，
#      故只取 hls 条目，避免出现一张「1080P」标签却下到 240P 的卡。
#   2. pornhub 的 HLS 是 fMP4（.m4s 分片 + EXT-X-MAP 初始化段），
#      拼接由 utils.xvideos.hls_segments 处理，本模块只负责给清单地址。
# 本模块不依赖 Qt，便于单测与命令行自检。
# ---------------------------------------------------------------------------

from __future__ import annotations

import html as _html
import json
import re
from typing import Callable, List, Optional, Tuple

from utils.xvideos import parse_hls_master

PH_REFERER = "https://www.pornhub.com/"
PH_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# 视频页：view_video.php?viewkey=xxx，embed/xxx 一并收
_PH_URL_RE = re.compile(
    r"https?://(?:[\w-]+\.)?pornhub\.com/(?:view_video\.php\?(?:[^\s\"'<>\u4e00-\u9fff]*&(?:amp;)?)?viewkey="
    r"[0-9a-zA-Z]+|embed/[0-9a-zA-Z]+)"
    r"(?=[/?#&;\"'<\s\u4e00-\u9fff，。！？、；：]|$)",
    re.I,
)

_LD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
_ISO_DUR_RE = re.compile(
    r"^P(?:(?P<d>\d+)D)?T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+(?:\.\d+)?)S)?$",
    re.I,
)


class PornhubError(Exception):
    """解析失败（页面不存在 / 无播放器数据 / 无可下载地址）。"""


def extract_pornhub_url(text: str) -> str:
    """从文本里取第一条 pornhub 视频页链接；不是视频页（首页、频道页）返回空串。"""
    m = _PH_URL_RE.search(text or "")
    if not m:
        return ""
    return _html.unescape(m.group(0)).rstrip(").,;]}\"'")


def looks_like_pornhub(text: str) -> bool:
    """是否含 pornhub 视频页链接。"""
    return bool(extract_pornhub_url(text))


def _meta(body: str, prop: str) -> str:
    for mm in re.finditer(
        r'<meta\s+(?:property|name)=["\']%s["\']\s+content="([^"]*)"' % re.escape(prop),
        body or "", re.I,
    ):
        return _html.unescape(mm.group(1)).strip()
    return ""


def _ld_video(body: str) -> dict:
    """取 ld+json 里的 VideoObject；解析失败返回空 dict。"""
    for m in _LD_RE.finditer(body or ""):
        raw = (m.group(1) or "").strip()
        if not raw or "VideoObject" not in raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("@type") == "VideoObject":
            return data
    return {}


def _iso_duration(value) -> int:
    """ISO8601 时长（PT00H11M12S）→ 秒；不是该格式返回 0。"""
    m = _ISO_DUR_RE.match(str(value or "").strip())
    if not m:
        return 0
    g = m.groupdict()
    try:
        return (
            int(g.get("d") or 0) * 86400
            + int(g.get("h") or 0) * 3600
            + int(g.get("m") or 0) * 60
            + int(float(g.get("s") or 0))
        )
    except (TypeError, ValueError):
        return 0


def _media_definitions(body: str) -> List[dict]:
    """页面内嵌 JSON 的 mediaDefinitions 数组。

    不能用正则 `\"mediaDefinitions\":(\\[.*?\\]),` —— 最后一条 mp4 里有
    `\"quality\":[],`，会在这里提前截断。改成按方括号配平截取。
    """
    m = re.search(r'"mediaDefinitions"\s*:\s*\[', body or "")
    if not m:
        return []
    start = m.end() - 1
    depth = 0
    for k in range(start, len(body)):
        c = body[k]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                try:
                    arr = json.loads(body[start:k + 1])
                except Exception:
                    return []
                return [d for d in arr if isinstance(d, dict)] if isinstance(arr, list) else []
    return []


def parse_pornhub_page(
    body: str, page_url: str, fetch_text: Optional[Callable[[str, str], str]] = None
) -> Tuple[dict, List[dict]]:
    """解析视频页 HTML。

    返回 (info, specs)：specs=[{label,url,ext,hls,height}]，第一项为最高画质。
    fetch_text(url, referer) -> str 用来拉 HLS 主清单；为空则只有主清单地址。
    """
    body = body or ""
    defs = _media_definitions(body)
    hls_defs = [d for d in defs if str(d.get("format") or "").lower() == "hls"]
    if not hls_defs and not defs:
        raise PornhubError(
            "页面里没有播放器数据（视频可能已删除、需要登录、被地区限制，或页面结构已变）"
        )
    if not hls_defs:
        raise PornhubError("未找到 HLS 画质地址（只有 mp4 跳转地址，无法解析）")

    ld = _ld_video(body)
    title = str(ld.get("name") or "").strip() or _meta(body, "og:title") or "pornhub"
    author = str(ld.get("author") or "").strip() or "pornhub"
    cover = _meta(body, "og:image") or str(ld.get("thumbnailUrl") or "").strip()
    info = {
        "title": _html.unescape(title),
        "author": author,
        "id": "",
        "cover": _html.unescape(cover),
        "duration": _iso_duration(ld.get("duration")),
        "platform": "pornhub",
        "referer": page_url,
        "page_url": page_url,
    }

    # 高度降序；同一档位只保留一条
    seen_h = set()
    ordered = []
    for d in hls_defs:
        try:
            h = int(d.get("height") or 0)
        except (TypeError, ValueError):
            h = 0
        if h in seen_h:
            continue
        seen_h.add(h)
        ordered.append((h, str(d.get("videoUrl") or "").replace("\\/", "/")))
    ordered.sort(key=lambda t: t[0], reverse=True)

    specs: List[dict] = []
    hls_err = ""
    for h, master in ordered:
        if not master:
            continue
        # 每档的主清单通常只有 1 个 variant；拉下来只为拿到真实分辨率做标签
        variants = []
        if fetch_text is not None:
            try:
                variants = parse_hls_master(fetch_text(master, page_url), master)
            except Exception as e:  # 清单拉不到时仍把主清单地址给出去
                hls_err = str(e) or repr(e)
        if variants:
            for v in variants:
                label_bits = [f"{h}p" if h else v["name"]]
                if v["res"]:
                    label_bits.append(v["res"])
                label_bits.append("HLS")
                specs.append({
                    "label": " · ".join(label_bits),
                    "url": v["url"],
                    "ext": ".mp4",
                    "hls": True,
                    "height": v["height"] or h,
                })
        else:
            specs.append({
                "label": f"{h}p · HLS" if h else "HLS",
                "url": master,
                "ext": ".mp4",
                "hls": True,
                "height": h,
            })
    if not specs:
        raise PornhubError(
            "未找到可下载的视频地址" + (f"（HLS 清单：{hls_err}）" if hls_err else "")
        )
    info["hls_error"] = hls_err
    return info, specs


def fetch_text(url: str, referer: str = PH_REFERER, timeout: float = 30, session=None) -> str:
    """GET 文本（requests 默认读取系统代理）。404 给出明确提示。"""
    if session is None:
        import requests  # 延迟导入：本模块可在无 requests 环境下做纯解析
        session = requests
    r = session.get(
        url,
        headers={
            "User-Agent": PH_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": referer or PH_REFERER,
        },
        timeout=timeout,
    )
    if r.status_code == 404:
        raise PornhubError("视频不存在或已被删除（HTTP 404）")
    if r.status_code in (401, 403, 451):
        raise PornhubError(f"站点拒绝访问（HTTP {r.status_code}），可能需要代理或被地区限制")
    r.raise_for_status()
    # 页面是 UTF-8；requests 已按响应头填好 r.encoding，别用 apparent_encoding
    # （它对这种大页面会猜成 MacRoman，标题会变乱码）
    r.encoding = r.encoding or "utf-8"
    return r.text or ""


def parse_pornhub(url_text: str, session=None, log_cb=None) -> Tuple[dict, List[dict]]:
    """从链接文本解析：拉页面 → 解析 → 逐档拉 HLS 主清单。"""
    url = extract_pornhub_url(url_text)
    if not url:
        raise PornhubError("不是 pornhub 视频页链接（需要 pornhub.com/view_video.php?viewkey=…）")
    if log_cb:
        log_cb(f"识别 Pornhub 链接：{url}")
        log_cb("拉取视频页 HTML…")
    body = fetch_text(url, PH_REFERER, session=session)
    info, specs = parse_pornhub_page(
        body, url, lambda u, ref: fetch_text(u, ref, timeout=20, session=session)
    )
    if log_cb and info.get("hls_error"):
        log_cb(f"部分 HLS 清单读取失败：{info['hls_error']}", "warn")
    return info, specs