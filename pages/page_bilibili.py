# pages/page_bilibili.py
# B站（哔哩哔哩）下载 —— 嵌入「视频下载」页的平台子页
# 布局对齐抖音：解析与下载卡 → 媒体内容卡 → 运行日志
# 引擎：yt-dlp（可选依赖；未安装时给出安装提示，不阻塞其它页面）
# 说明：抖音页走自研 HTTP 解析；B站与 YouTube 一样走 yt-dlp 官方提取器
#       （BiliBili / Bangumi 等）。Cookie（SESSDATA）可提升大会员/高画质可用性。

import os
import re
import time
import shutil
from concurrent.futures import ThreadPoolExecutor

from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QTextCursor
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QTextEdit, QFileDialog, QFrame, QProgressBar,
    QApplication, QMessageBox, QScrollArea,
)

from pages.page_douyin import MediaCard, MediaItem, _ElideLabel, DouyinProgressLine

from styles.style_all import (
    theme,
    tk,
    install_card_title,
    restyle_card_title,
    restyle_card_frame,
    make_card,
    apply_folder_path_edit,
    restyle_folder_path_edit,
    apply_mini_button,
    apply_simple_record,
    MEDIUM_BUTTON_H,
    CARD_TOP_GAP,
    CARD_LEFT_GAP,
    CARD_RIGHT_GAP,
    CARD_BOTTOM_GAP,
)
from utils.cursor_toast import (
    show_cursor_toast, in_batch_download, batch_progress_prefix,
)
from utils.download_watchdog import (
    DownloadWatchdog, StalledDownloadError, find_socket,
)

# ── yt-dlp 探测 ──────────────────────────────────────────────────────────────
try:
    import yt_dlp
    HAS_YTDLP = True
    YTDLP_VER = getattr(getattr(yt_dlp, "version", None), "__version__", "?")
except ImportError:
    yt_dlp = None
    HAS_YTDLP = False
    YTDLP_VER = ""

# EJS 挑战脚本包（可选；没有也能靠 remote_components 拉取）
try:
    import yt_dlp_ejs  # noqa: F401
    HAS_YTDLP_EJS = True
except ImportError:
    HAS_YTDLP_EJS = False


def _silence_ytdlp_python_deprecation():
    """屏蔽 yt-dlp 的「Python 3.10 deprecated」刷屏。

    注意：YoutubeDL.deprecated_feature() 会无条件 to_stderr，
    设 quiet / no_warnings / logger 都挡不住，只能从源头让
    _get_system_deprecation() 对「仅版本建议」返回 None。
    真正的不兼容（版本过低）提示仍会保留。

    另：`import yt_dlp.YoutubeDL` 在包已加载后可能拿到的是 **类**
    而不是子模块，所以必须用 importlib 取真正的模块对象。
    """
    if not HAS_YTDLP:
        return
    try:
        import importlib
        import yt_dlp.update as _upd
        _ydl_mod = importlib.import_module("yt_dlp.YoutubeDL")
    except Exception:
        return
    orig = getattr(_upd, "_get_system_deprecation", None)
    if not callable(orig) or getattr(orig, "_desktop_assistant_patched", False):
        return

    def _wrapped():
        msg = orig()
        if not msg:
            return None
        # 仅吞掉「Support for Python version x.y has been deprecated」这类建议
        if "Python version" in msg and "has been deprecated" in msg:
            return None
        return msg

    _wrapped._desktop_assistant_patched = True  # type: ignore[attr-defined]
    _upd._get_system_deprecation = _wrapped
    # YoutubeDL.py 里是 from .update import _get_system_deprecation，需改模块命名空间
    try:
        _ydl_mod._get_system_deprecation = _wrapped
    except Exception:
        pass


_silence_ytdlp_python_deprecation()


# 自动扫描 Cookie 时优先匹配的文件名（用户导出扩展默认名）
_BILI_COOKIE_PREFERRED_NAMES = (
    "www.bilibili.com_cookies.txt",
    "bilibili.com_cookies.txt",
    "bilibili_cookies.txt",
    "cookies.txt",
)


def _default_download_dirs() -> list:
    """常见下载目录：保存位置 / 用户 Downloads / 下载。"""
    dirs, seen = [], set()

    def _add(p):
        p = os.path.normpath(os.path.expanduser(p or ""))
        if not p:
            return
        key = os.path.normcase(p)
        if key in seen:
            return
        if os.path.isdir(p):
            seen.add(key)
            dirs.append(p)

    home = os.path.expanduser("~")
    for name in ("Downloads", "下载"):
        _add(os.path.join(home, name))
    up = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
    if up:
        for name in ("Downloads", "下载"):
            _add(os.path.join(up, name))
    return dirs


def _file_has_bilibili_cookies(path: str) -> bool:
    """粗判 Netscape cookie 文件是否含 bilibili 域。"""
    try:
        if not path or not os.path.isfile(path):
            return False
        if os.path.getsize(path) > 2 * 1024 * 1024:
            return False
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(65536).lower()
        return (
            "bilibili.com" in head
            or "biliapi" in head
            or ".bili." in head
            or "b23.tv" in head
        )
    except Exception:
        return False


def _score_bilibili_cookie_file(path: str) -> int:
    """评分：优先完整登录态（SESSDATA）+ 字段多。找不到有效内容返回 -1。"""
    if not _file_has_bilibili_cookies(path):
        return -1
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except Exception:
        return -1
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    score = len(lines)
    # B站关键 Cookie：SESSDATA=登录态，bili_jct=CSRF，DedeUserID=用户
    strong_keys = (
        "SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5",
        "sid", "buvid3", "buvid4",
    )
    for k in strong_keys:
        if k in text:
            score += 200
    base = os.path.basename(path).lower()
    for i, pref in enumerate(_BILI_COOKIE_PREFERRED_NAMES):
        if base == pref.lower():
            score += 500 - i * 50
            break
    if "bilibili" in base or "bili" in base:
        score += 100
    return score


def find_bilibili_cookie_file(search_dirs) -> str:
    """在目录顶层找最合适的 B站 Netscape Cookie。

    优先：www.bilibili.com_cookies.txt 等常见导出名；
    其次：任意含 bilibili.com 的 .txt。
    找不到返回 ""。
    """
    # 1) 优先精确文件名
    for d in search_dirs or []:
        if not d or not os.path.isdir(d):
            continue
        for name in _BILI_COOKIE_PREFERRED_NAMES:
            p = os.path.join(d, name)
            if os.path.isfile(p) and _file_has_bilibili_cookies(p):
                return os.path.normpath(p)

    # 2) 扫全部 .txt 评分
    candidates = []
    for d in search_dirs or []:
        if not d or not os.path.isdir(d):
            continue
        try:
            names = os.listdir(d)
        except Exception:
            continue
        for name in names:
            if not name.lower().endswith(".txt"):
                continue
            low = name.lower()
            if low in ("readme.txt", "license.txt", "changelog.txt"):
                continue
            path = os.path.join(d, name)
            if not os.path.isfile(path):
                continue
            sc = _score_bilibili_cookie_file(path)
            if sc < 0:
                continue
            try:
                mtime = os.path.getmtime(path)
            except Exception:
                mtime = 0
            candidates.append((sc, mtime, path))
    if not candidates:
        return ""
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return os.path.normpath(candidates[0][2])


# B站链接：BV/av 完整 URL / b23 短链 / 番剧 / 分享文案中的 URL
_BILI_URL_RE = re.compile(
    r"(https?://(?:(?:www|m|t)\.)?(?:bilibili\.com|b23\.tv|bili2233\.cn|bilibili\.tv)"
    r"/[^\s<>\"']+)",
    re.I,
)
_BILI_BARE_RE = re.compile(
    r"((?:(?:www|m|t)\.)?(?:bilibili\.com|b23\.tv)/[^\s<>\"']+)",
    re.I,
)
# 纯 BV/av 号（分享文案常见）
_BILI_ID_RE = re.compile(r"\b(BV[0-9A-Za-z]{10})\b|\b(av\d+)\b", re.I)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m|\[[0-9;]*m")


def extract_bilibili_url(text: str) -> str:
    """从纯链接、BV/av 号或分享文字中提取第一条 B站 URL。"""
    text = (text or "").strip()
    if not text:
        raise ValueError("空文本")
    m = _BILI_URL_RE.search(text)
    if m:
        return m.group(1).rstrip(").,];'\"")
    m = _BILI_BARE_RE.search(text)
    if m:
        return "https://" + m.group(1).rstrip(").,];'\"")
    m = _BILI_ID_RE.search(text)
    if m:
        vid = m.group(1) or m.group(2)
        return f"https://www.bilibili.com/video/{vid}"
    low = text.lower()
    if "bilibili.com" in low or "b23.tv" in low:
        token = text.split()[0].strip()
        if not token.startswith("http"):
            token = "https://" + token
        return token.rstrip(").,];'\"")
    raise ValueError(
        "未找到有效的 B站链接\n\n支持：\n"
        "· https://www.bilibili.com/video/BVxxxxxxxx\n"
        "· https://www.bilibili.com/video/avxxxxxxxx\n"
        "· https://b23.tv/xxxxx（短链）\n"
        "· 番剧：https://www.bilibili.com/bangumi/play/epxxxxx\n"
        "· 纯 BV/av 号或含链接的分享文案"
    )


# 解析前占位；真正选项由 build_quality_options(info) 在解析后生成
# 无解析信息时的兜底（自动下载前应已解析）
# 末尾落到 best，适配 Android 只给 progressive 单文件的情况
_FALLBACK_BEST = ("最佳画质", "best/bv*+ba/b", False)
_FALLBACK_AUDIO = ("仅音频 (m4a/mp3)", "ba/bestaudio/b/best", True)


# ═══════════════════════════════════════════════════════════════════════════
#  下载流程（两阶段，必须按此设计，禁止「解析一套、下载另一套」）
#  ---------------------------------------------------------------------------
#  1) 解析 extract_with_strategies(url, cookie)
#     · 多策略依次尝试（移动端无 Cookie / 网页+Cookie / 默认…）
#     · 任一策略拿到「可下载 formats」即成功
#     · 返回 info + extract_profile（策略名 / 是否 Cookie / player_client）
#  2) 画质 build_quality_options(info)
#     · 用解析结果里的真实 format_id 生成选项（默认第一项=最佳）
#  3) 下载 download（复用 extract_profile + 选中 format_id）
#     · 禁止下载时再换一套客户端，否则会出现「解析看似成功/下载又 bot」
# ═══════════════════════════════════════════════════════════════════════════

# B站策略：有 Cookie 优先（大会员/高画质/部分地区）；无 Cookie 兜底公开稿件。
# 不需要 YouTube 那套 player_client 切换。
EXTRACT_STRATEGIES = [
    {
        "name": "默认 + Cookie",
        "use_cookie": True,
        "extractor_args": None,
    },
    {
        "name": "默认（无 Cookie）",
        "use_cookie": False,
        "extractor_args": None,
    },
]


def _is_video_fmt(f: dict) -> bool:
    if (f.get("vcodec") or "none") == "none":
        return False
    # 排除 storyboard / 预览图伪视频轨
    note = str(f.get("format_note") or "").lower()
    if "storyboard" in note or "preview" in note:
        return False
    fid = str(f.get("format_id") or "")
    if fid.startswith("sb"):
        return False
    return True


def _is_audio_fmt(f: dict) -> bool:
    return (f.get("acodec") or "none") != "none"


def _fmt_dims(f: dict) -> tuple:
    """返回 (width, height)，非法值归 0。"""
    try:
        w = int(f.get("width") or 0)
    except (TypeError, ValueError):
        w = 0
    try:
        h = int(f.get("height") or 0)
    except (TypeError, ValueError):
        h = 0
    # 偶发只有 resolution 字符串 "1080x1920"
    if (not w or not h) and f.get("resolution"):
        m = re.match(r"(\d+)\s*x\s*(\d+)", str(f.get("resolution")), re.I)
        if m:
            try:
                w = w or int(m.group(1))
                h = h or int(m.group(2))
            except (TypeError, ValueError):
                pass
    return max(0, w), max(0, h)


def _fmt_short_side(f: dict) -> int:
    """画质「p」语义用短边：1080x1920 竖屏 = 1080p，1920x1080 横屏 = 1080p。

    旧逻辑只用 height，会把竖屏 1080x1920 标成「1920p」，
    且 height<=1080 过滤会把真正的竖屏 1080p 整档丢掉，只剩 608x1080 之类。
    """
    w, h = _fmt_dims(f)
    if w > 0 and h > 0:
        return min(w, h)
    return h or w or 0


def _fmt_long_side(f: dict) -> int:
    w, h = _fmt_dims(f)
    if w > 0 and h > 0:
        return max(w, h)
    return h or w or 0


def _fmt_pixels(f: dict) -> int:
    w, h = _fmt_dims(f)
    if w > 0 and h > 0:
        return w * h
    s = _fmt_short_side(f)
    return s * s if s else 0


def _fmt_quality_key(f: dict) -> tuple:
    """选画质排序键：短边 > 像素 > 码率（越高越好）。"""
    try:
        tbr = float(f.get("tbr") or 0)
    except (TypeError, ValueError):
        tbr = 0.0
    # 同分辨率略优先 mp4/avc1，合并兼容性更好
    ext = (f.get("ext") or "").lower()
    vcodec = (f.get("vcodec") or "").lower()
    compat = 0
    if ext in ("mp4", "m4v"):
        compat += 2
    if "avc" in vcodec or vcodec.startswith("avc1"):
        compat += 1
    return (_fmt_short_side(f), _fmt_pixels(f), tbr, compat)


def _fmt_within_cap(f: dict, cap: int) -> bool:
    """画质档位 cap（如 1080）是否覆盖该轨：以短边为准。"""
    if cap is None:
        return True
    s = _fmt_short_side(f)
    if s > 0:
        return s <= cap
    # 缺宽高时退化：仅看 height（旧字段）
    try:
        h = int(f.get("height") or 0)
    except (TypeError, ValueError):
        h = 0
    return h <= cap if h else False


def _fmt_has_media_url(f: dict) -> bool:
    """是否具备 yt-dlp 可下载的线索（含 DASH/HLS 分片，不限 progressive 直链）。"""
    if f.get("ext") in ("mhtml", "jpg", "png", "webp"):
        return False
    if f.get("protocol") == "mhtml":
        return False
    if f.get("url") or f.get("fragment_base_url") or f.get("fragments") or f.get("manifest_url"):
        return True
    # 部分客户端只填 protocol + format_id，二次 extract 仍可下
    proto = (f.get("protocol") or "").lower()
    if proto in (
        "https", "http", "http_dash_segments", "m3u8", "m3u8_native",
        "dash", "http_dash_segments_generator",
    ):
        return bool(f.get("format_id"))
    return False


def _usable_formats(info: dict) -> list:
    out = []
    for f in (info or {}).get("formats") or []:
        if not (_is_video_fmt(f) or _is_audio_fmt(f)):
            continue
        if not _fmt_has_media_url(f):
            continue
        out.append(f)
    return out


def _max_video_height(info: dict) -> int:
    """兼容旧调用：返回最大「画质 p」（短边），不是裸 height。"""
    return _max_video_quality(info)


def _max_video_quality(info: dict) -> int:
    """本视频可用的最高画质档（短边像素，竖屏 1080x1920 → 1080）。"""
    q = 0
    for f in _usable_formats(info):
        if _is_video_fmt(f):
            q = max(q, _fmt_short_side(f))
    return q


def _best_video_dims_label(info: dict) -> str:
    """形如 1080x1920，用于日志；找不到则空串。"""
    best = None
    best_key = (-1, -1, -1.0, -1)
    for f in _usable_formats(info):
        if not _is_video_fmt(f):
            continue
        key = _fmt_quality_key(f)
        if key > best_key:
            best_key = key
            best = f
    if not best:
        return ""
    w, h = _fmt_dims(best)
    if w and h:
        return f"{w}x{h}"
    s = _fmt_short_side(best)
    return f"{s}p" if s else ""


def _has_adaptive(info: dict) -> bool:
    """是否有分离的视频轨+音频轨（可拼出高于 progressive 的画质）。"""
    fmts = _usable_formats(info)
    has_v = any(_is_video_fmt(f) and not _is_audio_fmt(f) for f in fmts)
    has_a = any(_is_audio_fmt(f) and not _is_video_fmt(f) for f in fmts)
    return has_v and has_a


def _score_extract(info: dict) -> int:
    """策略打分：分辨率（短边/像素）优先，自适应流加分，格式条数次之。"""
    usable = _usable_formats(info)
    max_q = _max_video_quality(info)
    max_px = 0
    for f in usable:
        if _is_video_fmt(f):
            max_px = max(max_px, _fmt_pixels(f))
    n = len(usable)
    # 像素量级防止「高 height 窄 width」虚高
    score = max_q * 10000 + (max_px // 1000) + n * 10
    if _has_adaptive(info):
        score += 50000  # 有自适应通常远强于仅 18 号 360/640p
    return score


def _orientation_cap_selector(cap: int) -> str:
    """构造横竖屏都正确的 yt-dlp 画质上限串。

    「1080p」= 短边 ≤1080：
      · 横屏 1920x1080：height<=1080 且 width 可到 ~1920
      · 竖屏 1080x1920：width<=1080 且 height 可到 ~1920
    旧串 bv*[height<=1080] 会把竖屏 1080x1920 整档排除。
    """
    long_max = int(cap * 16 / 9) + 16  # 1080 → 1936，覆盖 1920
    return (
        f"bv*[height<={cap}][width<={long_max}]+ba/"
        f"bv*[width<={cap}][height<={long_max}]+ba/"
        f"b[height<={cap}]/b[width<={cap}]/"
        f"best[height<={cap}]/best[width<={cap}]/best"
    )


def build_quality_options(info: dict) -> list:
    """根据解析到的 formats 生成画质列表。

    返回 [(label, format_str, is_audio_only), ...]，**第一项永远是最佳画质**。
    有自适应流时优先锁定具体 format_id（竖/横屏按短边算 p 数）；
    仅 progressive 时才锁单文件 format_id。
    """
    fmts = _usable_formats(info)
    progressive = [f for f in fmts if _is_video_fmt(f) and _is_audio_fmt(f)]
    video_only = [f for f in fmts if _is_video_fmt(f) and not _is_audio_fmt(f)]
    audio_only = [f for f in fmts if _is_audio_fmt(f) and not _is_video_fmt(f)]
    max_q = _max_video_quality(info)
    adaptive = bool(video_only and audio_only)
    dims_lbl = _best_video_dims_label(info)

    def _best_prog(cap=None):
        cands = progressive
        if cap is not None:
            cands = [f for f in cands if _fmt_within_cap(f, cap)]
        if not cands:
            return None
        return max(cands, key=_fmt_quality_key)

    def _best_v(cap=None):
        cands = video_only
        if cap is not None:
            cands = [f for f in cands if _fmt_within_cap(f, cap)]
        if not cands:
            return None
        return max(cands, key=_fmt_quality_key)

    def _best_a():
        if not audio_only:
            return None
        return max(audio_only, key=lambda f: (f.get("tbr") or 0, f.get("abr") or 0))

    def _selector_for_cap(cap=None) -> str:
        """构造 yt-dlp format 串：自适应优先，再回退 progressive / best。"""
        parts = []
        bv, ba = _best_v(cap), _best_a()
        if bv and ba and bv.get("format_id") and ba.get("format_id"):
            parts.append(f"{bv['format_id']}+{ba['format_id']}")
        if cap:
            parts.append(_orientation_cap_selector(cap))
        else:
            parts.append("bv*+ba/b/bestvideo*+bestaudio/best")
        bp = _best_prog(cap)
        if bp and bp.get("format_id"):
            parts.append(str(bp["format_id"]))
        parts.append("best")
        # 去重保序
        seen, out = set(), []
        for p in parts:
            if p not in seen:
                seen.add(p)
                out.append(p)
        return "/".join(out)

    opts = []
    if adaptive or max_q or progressive:
        if max_q and dims_lbl and "x" in dims_lbl:
            label = f"最佳画质（{max_q}p · {dims_lbl}）"
        elif max_q:
            label = f"最佳画质（{max_q}p）"
        else:
            label = "最佳画质"
        opts.append((label, _selector_for_cap(None), False))
        for cap in (2160, 1440, 1080, 720, 480, 360):
            if max_q > cap:
                opts.append((f"{cap}p 及以下", _selector_for_cap(cap), False))

    ba = _best_a()
    if ba and ba.get("format_id"):
        ext = ba.get("ext") or "m4a"
        opts.append((f"仅音频 ({ext})", f"{ba['format_id']}/ba/bestaudio/b", True))
    elif progressive or adaptive:
        opts.append(_FALLBACK_AUDIO)

    if not opts:
        opts.append(_FALLBACK_BEST)
    return opts


def build_media_specs(info: dict) -> list:
    """解析结果 → 媒体内容卡规格（对齐抖音 MediaItem 字段）。"""
    title = (info or {}).get("title") or "B站视频"
    thumb = (info or {}).get("thumbnail") or ""
    url = (info or {}).get("webpage_url") or ""
    dur = (info or {}).get("duration_str") or ""
    short = title if len(title) <= 18 else (title[:16] + "…")
    specs = [{
        "kind": "video",
        "label": short,
        "url": url,
        "ext": "mp4",
        "thumb_url": thumb,
        "default_checked": True,
        "hint": f"视频 · {dur}" if dur and dur != "—" else "视频",
    }]
    # 有独立音频轨，或 progressive 也可抽音频时展示音频卡
    fmts = _usable_formats(info)
    has_audio = any(_is_audio_fmt(f) for f in fmts)
    if has_audio:
        specs.append({
            "kind": "audio",
            "label": "音频轨道",
            "url": url,
            "ext": "m4a",
            "thumb_url": thumb,
            "default_checked": False,
            "hint": "仅音频",
        })
    return specs


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "").strip()


def _resolve_exe(path: str):
    """解析 WinGet 符号链接等到真实 exe，避免提权/子进程跟丢 soft link。"""
    if not path:
        return None
    try:
        if not os.path.exists(path):
            return None
        real = os.path.realpath(path)
        if real and os.path.isfile(real):
            return real
        if os.path.isfile(path):
            return path
    except Exception:
        if os.path.isfile(path):
            return path
    return None


def _scan_winget_package_exe(exe_name: str):
    """在 %LOCALAPPDATA%\\Microsoft\\WinGet\\Packages 下扫一层找 exe。"""
    local = os.environ.get("LOCALAPPDATA", "")
    root = os.path.join(local, "Microsoft", "WinGet", "Packages")
    if not os.path.isdir(root):
        return None
    try:
        for entry in os.listdir(root):
            # DenoLand.Deno_... / OpenJS.NodeJS_...
            base = os.path.join(root, entry)
            if not os.path.isdir(base):
                continue
            cand = os.path.join(base, exe_name)
            hit = _resolve_exe(cand)
            if hit:
                return hit
            # 偶发多一层目录
            try:
                for sub in os.listdir(base):
                    cand2 = os.path.join(base, sub, exe_name)
                    hit = _resolve_exe(cand2)
                    if hit:
                        return hit
            except Exception:
                pass
    except Exception:
        pass
    return None


def _which_exe(name: str):
    """PATH + Windows 常见安装路径查找可执行文件。"""
    p = shutil.which(name)
    hit = _resolve_exe(p) if p else None
    if hit:
        return hit
    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = []
    bare = name if name.lower().endswith(".exe") else f"{name}.exe"
    if name.lower() in ("deno", "deno.exe"):
        candidates = [
            os.path.join(local, "Microsoft", "WinGet", "Links", "deno.exe"),
            os.path.join(home, ".deno", "bin", "deno.exe"),
            r"C:\Program Files\Deno\deno.exe",
        ]
    elif name.lower() in ("node", "node.exe"):
        candidates = [
            os.path.join(local, "Microsoft", "WinGet", "Links", "node.exe"),
            r"C:\Program Files\nodejs\node.exe",
            os.path.join(local, "Programs", "node", "node.exe"),
        ]
    elif name.lower() in ("ffmpeg", "ffmpeg.exe"):
        candidates = [
            os.path.join(local, "Microsoft", "WinGet", "Links", "ffmpeg.exe"),
            r"C:\ffmpeg\bin\ffmpeg.exe",
            os.path.join(home, "scoop", "shims", "ffmpeg.exe"),
        ]
    for c in candidates:
        hit = _resolve_exe(c)
        if hit:
            return hit
    # 最后扫 WinGet Packages（Links 是 0 字节 symlink，提权后偶发异常）
    if name.lower().startswith("deno") or name.lower().startswith("node") or name.lower().startswith("ffmpeg"):
        hit = _scan_winget_package_exe(bare)
        if hit:
            return hit
    return None


def find_js_runtime():
    """返回 (runtime_name, path) 或 (None, None)。优先 deno（yt-dlp 默认）。"""
    for name in ("deno", "node"):
        p = _which_exe(name)
        if p:
            return name, p
    return None, None


def _ensure_runtime_on_path(exe_path: str):
    """把 runtime 所在目录塞进当前进程 PATH，方便 yt-dlp 子进程再 which 一次。"""
    if not exe_path:
        return
    d = os.path.dirname(exe_path)
    if not d or not os.path.isdir(d):
        return
    cur = os.environ.get("PATH", "")
    parts = cur.split(os.pathsep) if cur else []
    if d not in parts:
        os.environ["PATH"] = d + os.pathsep + cur


def _js_runtimes_dict():
    """构造 yt-dlp js_runtimes 参数；把绝对路径塞进去，避免 GUI 启动时 PATH 不全。"""
    name, path = find_js_runtime()
    if not name:
        # 仍声明 deno，让 yt-dlp 自己在 PATH 里再找一次
        return {"deno": {}}
    _ensure_runtime_on_path(path)
    return {name: {"path": path}}


def _base_ydl_opts(
    cookie_path: str = "",
    *,
    download: bool = False,
    use_cookie: bool = True,
    extractor_args=None,
) -> dict:
    """B站通用选项：Referer 防盗链；Cookie 提升大会员/高画质。

    noplaylist=True：多 P 视频默认只下 URL 指定分 P（?p=2），避免整合集。
    B站不依赖 Deno/EJS 解签名；ffmpeg 用于 DASH 音视频合并。
    """
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        # 解析阶段允许空 formats，由策略循环自己判断成败
        "ignore_no_formats_error": not download,
        "no_color": True,
        "http_headers": {
            "Referer": "https://www.bilibili.com",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        },
    }
    if not download:
        opts["skip_download"] = True
    cookie_path = (cookie_path or "").strip()
    if use_cookie and cookie_path and os.path.isfile(cookie_path):
        opts["cookiefile"] = cookie_path
    if extractor_args:
        opts["extractor_args"] = extractor_args
    return opts


def _probe_media_url(url: str, headers=None, timeout=8) -> bool:
    """HEAD/Range 探测直链是否真能下（过滤 403 空壳 format）。"""
    if not url:
        return False
    headers = dict(headers or {})
    headers.setdefault(
        "User-Agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    )
    headers.setdefault("Referer", "https://www.bilibili.com/")
    # 只要前几个字节，避免下完整视频
    headers["Range"] = "bytes=0-1023"
    try:
        import requests as req_lib
        r = req_lib.get(url, headers=headers, timeout=timeout, stream=True, verify=False)
        code = r.status_code
        try:
            chunk = next(r.iter_content(1024), b"")
        except Exception:
            chunk = b""
        r.close()
        return code in (200, 206) and bool(chunk)
    except Exception:
        try:
            import urllib.request
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read(64)
                return bool(data)
        except Exception:
            return False


def _has_downloadable_formats(info: dict) -> bool:
    """是否至少有一条可交给 yt-dlp 的音/视频轨（含 DASH 自适应）。"""
    if _usable_formats(info):
        return True
    if (info or {}).get("url") and (info.get("vcodec") or info.get("acodec")):
        return True
    return False


def _strategy_accept(info: dict, log=None) -> bool:
    """判断本策略结果是否可用。

    - 有自适应（视频轨+音频轨）：直接接受（高画质靠 yt-dlp 合并，不强制直链探测）
    - 仅 progressive：至少一条直链探测通过，否则多半是 403 空壳
      （多条直链并发探测，而不是一条条同步等到超时才试下一条）
    """
    if not _has_downloadable_formats(info):
        return False
    if _has_adaptive(info):
        return True
    fmts = [f for f in _usable_formats(info) if f.get("url")]
    if not fmts:
        if log:
            log("  → 仅有 progressive 且无直链可探测，本策略作废", "warn")
        return False
    with ThreadPoolExecutor(max_workers=min(6, len(fmts))) as pool:
        futs = [
            pool.submit(_probe_media_url, f.get("url") or "", f.get("http_headers"))
            for f in fmts
        ]
        ok = any(fut.result() for fut in futs)
    if not ok and log:
        log("  → 仅有 progressive 且直链探测失败（403），本策略作废", "warn")
    return ok


def _run_one_extract_strategy(url: str, cookie_path: str, strat: dict, log=None) -> dict:
    """线程池工作函数：跑一套提取策略，不抛异常，结果打包成 dict 返回。"""
    name = strat["name"]
    opts = _base_ydl_opts(
        cookie_path=cookie_path,
        download=False,
        use_cookie=bool(strat["use_cookie"]),
        extractor_args=strat.get("extractor_args"),
    )
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            if log:
                log(f"策略「{name}」→ 空结果", "warn")
            return {"ok": False}
        if info.get("_type") == "playlist" and info.get("entries"):
            info = info["entries"][0] or info
        n_all = len(info.get("formats") or [])
        n_ok = len(_usable_formats(info))
        max_q = _max_video_quality(info)
        dims = _best_video_dims_label(info)
        adaptive = _has_adaptive(info)
        if not _strategy_accept(info, log=log):
            if log:
                extra = f" · {dims}" if dims else ""
                if n_all:
                    log(f"策略「{name}」→ 不可用（formats={n_all}，可用={n_ok}，最高{max_q}p{extra}）", "warn")
                else:
                    log(f"策略「{name}」→ 无可用流（多为缩略图/空壳）", "warn")
            return {"ok": False}
        score = _score_extract(info)
        profile = {
            "name": name,
            "use_cookie": bool(strat["use_cookie"]),
            "cookie_path": cookie_path if strat["use_cookie"] else "",
            "extractor_args": strat.get("extractor_args"),
        }
        kind = "自适应" if adaptive else "progressive"
        if log:
            extra = f" · {dims}" if dims else ""
            log(f"策略「{name}」→ 候选可用：最高 {max_q}p{extra} · {n_ok} 条流 · {kind} · 评分 {score}", "ok")
        return {
            "ok": True, "score": score, "max_q": max_q, "n_ok": n_ok,
            "info": info, "profile": profile,
        }
    except Exception as e:
        msg = _strip_ansi(str(e).strip() or repr(e))
        if log:
            short = msg.split("\n")[0][:160]
            log(f"策略「{name}」→ 失败：{short}", "warn")
        return {"ok": False, "err": e}


def extract_with_strategies(url: str, cookie_path: str = "", log=None):
    """多策略解析，全部尝试后选分数最高的一个。

    以前是 for 循环逐套策略同步执行——每套都是一次完整的 yt-dlp 网络请求，
    串行等待非常慢。现在改用线程池并发跑（有限并发，避免瞬间打太多请求），
    总耗时约等于「策略数 / 并发数」轮里最慢的一轮，而不是所有策略耗时之和。

    返回 (info, extract_profile)
    """
    if not HAS_YTDLP:
        raise RuntimeError("未安装 yt-dlp")

    cookie_path = (cookie_path or "").strip()
    has_cookie = bool(cookie_path and os.path.isfile(cookie_path))
    strategies = [s for s in EXTRACT_STRATEGIES if (not s["use_cookie"]) or has_cookie]
    if not strategies:
        raise RuntimeError("没有可用的解析策略")

    if log:
        log(f"并发解析 {len(strategies)} 套策略…", "info")

    best = None  # (score, max_q, n_ok, info, profile)
    last_err = None
    max_workers = min(4, len(strategies))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = [
            pool.submit(_run_one_extract_strategy, url, cookie_path, strat, log)
            for strat in strategies
        ]
        for fut in futs:
            res = fut.result()
            if not res.get("ok"):
                if res.get("err"):
                    last_err = res["err"]
                continue
            if best is None or res["score"] > best[0]:
                best = (res["score"], res["max_q"], res["n_ok"], res["info"], res["profile"])

    if best is None:
        detail = (
            _strip_ansi(str(last_err).strip())
            if last_err
            else "所有策略均未拿到视频流"
        )
        raise RuntimeError(detail)

    score, max_q, n_ok, info, profile = best
    if log:
        dims = _best_video_dims_label(info)
        extra = f" · {dims}" if dims else ""
        log(
            f"采用策略「{profile['name']}」：最高 {max_q}p{extra} · {n_ok} 条可用流"
            + (" · 含自适应（可合并高清）" if _has_adaptive(info) else ""),
            "ok",
        )
    return info, profile


def _friendly_ytdlp_error(msg: str) -> str:
    msg = _strip_ansi(msg)
    low = msg.lower()
    tips = []
    ffmpeg_ok = bool(_which_exe("ffmpeg"))

    if "format is not available" in low or "only images are available" in low or "未拿到" in msg:
        tips.append(
            "拿不到可下载的视频流。可尝试：导入含 SESSDATA 的 Cookie；"
            "大会员专享/地区限制内容必须登录 Cookie。"
        )
    if "login" in low or "cookie" in low or "403" in low or "412" in low:
        tips.append(
            "可能需要登录态 Cookie。请：\n"
            "  1) 浏览器登录 https://www.bilibili.com\n"
            "  2) 用扩展「Get cookies.txt LOCALLY」导出 .txt\n"
            "  3) 本页加载 Cookie（建议含 SESSDATA）"
        )
    if "premium" in low or "会员" in msg or "大会员" in msg:
        tips.append("该清晰度可能需要大会员；请用大会员账号导出 Cookie。")
    if "private" in low or "权限" in msg:
        tips.append("私密/无权限视频无法下载。")
    if "geo" in low or "地区" in msg:
        tips.append("可能有地区限制，换网络或带对应区 Cookie 再试。")
    if not ffmpeg_ok:
        tips.append(
            "未检测到 ffmpeg。B站高清多为「画面+声音」分轨，合并需要 ffmpeg：\n"
            "  winget install Gyan.FFmpeg\n"
            "  装完后重启本程序。"
        )
    else:
        tips.append("ffmpeg 已就绪（可合并 DASH 高清）。")

    tips.append(
        "流程说明：解析成功后会锁定当时用的策略，下载必须用同一策略；"
        "画质下拉在解析后才生成，默认最佳。多 P 视频请在链接后加 ?p=序号。"
    )

    if tips:
        msg = msg + "\n\n—— 排查建议 ——\n" + "\n".join(f"· {t}" for t in tips)
    return msg


def _format_duration(sec) -> str:
    try:
        sec = int(sec or 0)
    except (TypeError, ValueError):
        return "—"
    if sec <= 0:
        return "—"
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ═══════════════════════════════════════════════════════════════════════════
#  后台线程
# ═══════════════════════════════════════════════════════════════════════════

class YtParseWorker(QThread):
    ok = pyqtSignal(dict)   # info dict（download=False）
    err = pyqtSignal(str)
    log = pyqtSignal(str, str)  # msg, level

    def __init__(self, url_text: str, cookie_path: str = ""):
        super().__init__()
        self.url_text = url_text
        self.cookie_path = (cookie_path or "").strip()

    def run(self):
        if not HAS_YTDLP:
            self.err.emit(
                "未安装 yt-dlp\n\n请在终端执行：\n"
                "  pip install -U yt-dlp\n\n"
                "安装后重启本程序即可。"
            )
            return
        try:
            url = extract_bilibili_url(self.url_text)
        except Exception as e:
            self.err.emit(str(e))
            return
        self.log.emit(f"提取链接：{url}", "info")
        if self.cookie_path and os.path.isfile(self.cookie_path):
            # 粗检 Cookie 是否像「已登录」——关键字段 SESSDATA
            try:
                raw = open(self.cookie_path, encoding="utf-8", errors="ignore").read()
                strong = any(
                    k in raw
                    for k in (
                        "SESSDATA",
                        "bili_jct",
                        "DedeUserID",
                    )
                )
                if strong:
                    self.log.emit(
                        "已配置 Cookie（检测到 SESSDATA/登录字段；"
                        "有 Cookie 策略优先使用）",
                        "ok",
                    )
                else:
                    self.log.emit(
                        "Cookie 文件可能不完整（未发现 SESSDATA/bili_jct 等登录字段）。"
                        "大会员/高画质内容可能失败——请在已登录 bilibili.com 后重新导出。",
                        "warn",
                    )
            except Exception:
                self.log.emit("已配置 Cookie 文件", "info")
        else:
            self.log.emit("未配置 Cookie，将尝试公开稿件解析（高画质可能受限）", "info")
        if not _which_exe("ffmpeg"):
            self.log.emit(
                "未检测到 ffmpeg — 高清 DASH 合并可能失败，建议 winget install Gyan.FFmpeg",
                "warn",
            )

        try:
            def _lg(msg, level="info"):
                self.log.emit(msg, level)

            info, profile = extract_with_strategies(
                url, self.cookie_path, log=_lg
            )
            title = info.get("title") or "(无标题)"
            dur = _format_duration(info.get("duration"))
            uploader = info.get("uploader") or info.get("channel") or "—"
            usable = _usable_formats(info)
            if not usable:
                raise RuntimeError("解析成功但无可用音视频流")
            max_q = _max_video_quality(info)
            dims = _best_video_dims_label(info)
            adaptive = _has_adaptive(info)

            # 标记 progressive 直链是否可探（高清自适应不依赖此项）
            # 并发探测，而不是逐条同步等待——格式多时能省下大量时间
            probe_targets = []  # (index, url, headers)
            for i, f in enumerate(usable):
                f_url = f.get("url") or ""
                has_frag = bool(
                    f.get("fragment_base_url") or f.get("fragments") or f.get("manifest_url")
                )
                if f_url and not has_frag:
                    probe_targets.append((i, f_url, f.get("http_headers")))

            probe_results = {}
            if probe_targets:
                with ThreadPoolExecutor(max_workers=min(8, len(probe_targets))) as pool:
                    futs = {
                        pool.submit(_probe_media_url, t[1], t[2]): t[0]
                        for t in probe_targets
                    }
                    for fut, idx in futs.items():
                        probe_results[idx] = fut.result()

            formats_full = []
            live_n = 0
            for i, f in enumerate(usable):
                f_url = f.get("url") or ""
                probe_ok = bool(probe_results.get(i, False))
                if probe_ok:
                    live_n += 1
                w, h = _fmt_dims(f)
                formats_full.append({
                    "format_id": str(f.get("format_id") or ""),
                    "width": w or f.get("width"),
                    "height": h or f.get("height"),
                    "vcodec": f.get("vcodec"),
                    "acodec": f.get("acodec"),
                    "ext": f.get("ext") or "mp4",
                    "tbr": f.get("tbr"),
                    "abr": f.get("abr"),
                    "fps": f.get("fps"),
                    "url": f_url,
                    "http_headers": f.get("http_headers") or {},
                    "probe_ok": probe_ok,
                    "protocol": f.get("protocol") or "",
                    "fragment_base_url": f.get("fragment_base_url") or "",
                    "manifest_url": f.get("manifest_url") or "",
                })

            self.log.emit(f"标题：{title}", "ok")
            dims_part = f" · {dims}" if dims else ""
            self.log.emit(
                f"时长 {dur} · 上传者 {uploader} · 可用流 {len(usable)} 条 · "
                f"最高 {max_q}p{dims_part} · "
                f"{'自适应可合并' if adaptive else 'progressive'} · "
                f"策略「{profile.get('name', '?')}」",
                "info",
            )
            if adaptive and max_q < 720:
                self.log.emit(
                    "提示：当前仅拿到较低分辨率自适应流；可换网络/更新 Cookie 后重试",
                    "warn",
                )
            elif not adaptive and max_q and max_q < 720:
                self.log.emit(
                    "提示：仅 progressive 低清流（常见于移动端接口）；"
                    "高清需网页端自适应流，请确认 Cookie 完整并重试",
                    "warn",
                )
            if live_n:
                self.log.emit(f"其中 {live_n} 条 progressive 直链探测通过", "info")

            quality_options = build_quality_options(info)
            if quality_options:
                self.log.emit(f"默认画质：{quality_options[0][0]}", "ok")

            payload = {
                "webpage_url": info.get("webpage_url") or url,
                "title": title,
                "duration": info.get("duration"),
                "duration_str": dur,
                "uploader": uploader,
                "thumbnail": info.get("thumbnail") or "",
                "ext": info.get("ext") or "mp4",
                "id": info.get("id") or "",
                "formats": formats_full,
                "quality_options": quality_options,
                "media_specs": build_media_specs({
                    "title": title,
                    "thumbnail": info.get("thumbnail") or "",
                    "webpage_url": info.get("webpage_url") or url,
                    "duration_str": dur,
                    "formats": usable,
                }),
                # 关键：下载必须复用
                "extract_profile": profile,
            }
            self.ok.emit(payload)
        except Exception as e:
            self.err.emit(_friendly_ytdlp_error(str(e).strip() or repr(e)))


def _safe_title_name(title: str, vid: str = "") -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", (title or "").strip())
    name = name.strip(" .") or "bilibili_video"
    if vid:
        return f"{name[:60]} [{vid}]"
    return name[:70]


def _pick_formats_for_selector(selector: str, formats_full: list):
    """从 format 串（如 18/best/... 或 137+140/best）解析出要下的 format 条目。

    只认选择器**第一段**锁定的具体 format_id（解析阶段选好的最高画质），
    绝不回退到串里后面的 best/18，避免「界面写 1080p、实际下到 360p」。

    返回 ('single', fmt_dict) | ('merge', v_fmt, a_fmt) | None
    None 表示缓存里没有可直链的条目 → 交给 yt-dlp 二次 extract。
    """
    if not selector or not formats_full:
        return None
    primary = (selector.split("/")[0] or "").strip()
    # 通配符段（bv* / best…）不能从缓存 id 映射
    if not primary or any(ch in primary for ch in "*[]") or primary in (
        "best", "b", "bv", "ba", "bestvideo", "bestaudio",
        "bestvideo*", "bestaudio*", "bv*", "ba*",
    ):
        return None
    by_id = {str(f.get("format_id")): f for f in formats_full if f.get("format_id")}

    if "+" in primary:
        # 仅允许 id+id；排除 bv*+ba 这类
        left, right = primary.split("+", 1)
        left, right = left.strip(), right.strip()
        if any(ch in left or ch in right for ch in "*[]") or not left or not right:
            return None
        vf, af = by_id.get(left), by_id.get(right)
        if not vf or not af:
            return None
        # 需要可 HTTP 直下的 url（DASH 分片留给 yt-dlp）
        if vf.get("url") and af.get("url"):
            return ("merge", vf, af)
        return None

    f = by_id.get(primary)
    if f and f.get("url"):
        return ("single", f)
    return None


def _format_dim_text(f: dict) -> str:
    if not f:
        return "?"
    w, h = _fmt_dims(f)
    if w and h:
        return f"{w}x{h}"
    s = _fmt_short_side(f)
    return f"{s}p" if s else "?"


def _http_download(url: str, dest: str, headers=None, cancel_flag=None, progress_cb=None,
                    watchdog=None, key=None, stall_msg: str = ""):
    """直链流式下载（解析时缓存的 format.url）。"""
    headers = dict(headers or {})
    if "User-Agent" not in headers and "user-agent" not in headers:
        headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    headers.setdefault("Referer", "https://www.bilibili.com/")
    tmp = dest + ".part"
    try:
        try:
            import requests as req_lib
            use_req = True
        except ImportError:
            use_req = False

        if use_req:
            # 不要强行覆盖 format 自带的 UA/Accept；googlevideo 对指纹很敏感
            with req_lib.get(
                url, headers=headers, stream=True, timeout=60, verify=False
            ) as r:
                r.raise_for_status()
                if watchdog:
                    watchdog.attach_socket(key, find_socket(r))
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                with open(tmp, "wb") as out:
                    for chunk in r.iter_content(chunk_size=256 * 1024):
                        if cancel_flag and cancel_flag[0]:
                            raise Exception("用户取消")
                        if watchdog and watchdog.is_dead(key):
                            raise StalledDownloadError(
                                stall_msg or f"{int(watchdog.stall_seconds)} 秒无响应，已视为无法下载"
                            )
                        if not chunk:
                            continue
                        out.write(chunk)
                        done += len(chunk)
                        if watchdog:
                            watchdog.touch(key)
                        if progress_cb and total:
                            progress_cb(int(done * 100 / total))
        else:
            import urllib.request
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                if watchdog:
                    watchdog.attach_socket(key, find_socket(resp))
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                with open(tmp, "wb") as out:
                    while True:
                        if cancel_flag and cancel_flag[0]:
                            raise Exception("用户取消")
                        if watchdog and watchdog.is_dead(key):
                            raise StalledDownloadError(
                                stall_msg or f"{int(watchdog.stall_seconds)} 秒无响应，已视为无法下载"
                            )
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
                        done += len(chunk)
                        if watchdog:
                            watchdog.touch(key)
                        if progress_cb and total:
                            progress_cb(int(done * 100 / total))
        os.replace(tmp, dest)
        return dest
    finally:
        if os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def _resolve_path(path: str, overwrite: bool) -> str:
    """根据 overwrite 标志处理同名文件：
    overwrite=True  → 返回原路径（调用方直接覆盖）
    overwrite=False → 若文件存在，自动递增序号 base_1.ext, base_2.ext…"""
    if overwrite or not os.path.isfile(path):
        return path
    base, ext = os.path.splitext(path)
    n = 1
    while os.path.isfile(path):
        path = f"{base}_{n}{ext}"
        n += 1
    return path


def _stem_taken(folder: str, stem: str) -> bool:
    try:
        for name in os.listdir(folder):
            st, _ext = os.path.splitext(name)
            if st == stem and os.path.isfile(os.path.join(folder, name)):
                return True
    except OSError:
        return False
    return False


def _ydl_numbered_stem(save_dir: str, stem: str) -> tuple:
    """yt-dlp 用：若 stem.ext 已占用，改用 stem_N。返回 (stem, used_rename)。"""
    if not _stem_taken(save_dir, stem):
        return stem, False
    n = 1
    while _stem_taken(save_dir, f"{stem}_{n}"):
        n += 1
    return f"{stem}_{n}", True


class YtDownloadWorker(QThread):
    progress = pyqtSignal(int, str)   # pct 0-100, status text
    ok = pyqtSignal(str)              # saved path or dir
    err = pyqtSignal(str)
    log = pyqtSignal(str, str)
    finished_clean = pyqtSignal()

    def __init__(
        self,
        url: str,
        save_dir: str,
        jobs=None,
        cookie_path: str = "",
        extract_profile=None,
        formats_full=None,
        video_meta=None,
        cancel_flag=None,
        overwrite: bool = True,
    ):
        """jobs: list[{"label","format","audio_only"}]，按序下载。
        extract_profile: 解析阶段锁定的策略。
        formats_full: 解析时缓存的带 url 的 formats（优先直链下载）。
        video_meta: {title, id} 用于文件名。
        overwrite: True=直接覆盖同名文件，False=自动递增序号重命名。
        """
        super().__init__()
        self.url = url
        self.save_dir = save_dir
        self.jobs = list(jobs or [])
        self.cookie_path = (cookie_path or "").strip()
        self.extract_profile = dict(extract_profile or {})
        self.formats_full = list(formats_full or [])
        self.video_meta = dict(video_meta or {})
        self._cancel = cancel_flag if cancel_flag is not None else [False]
        self._last_path = ""
        self._job_base = 0
        self._job_count = max(1, len(self.jobs))
        self._overwrite = bool(overwrite)
        self._wd = None          # 防卡死看门狗（run() 内创建）
        self._active_key = None  # 当前任务在看门狗中的 key

    def _finalize_dest(self, dest: str, canonical: str) -> str:
        """加序号另存后，按大小与已有同名文件比对：相同删新、不同都留。"""
        if self._overwrite or not dest or not canonical:
            return dest
        try:
            if os.path.normcase(os.path.abspath(dest)) == os.path.normcase(
                os.path.abspath(canonical)
            ):
                return dest
        except Exception:
            return dest
        from utils.download_confirm import dedupe_by_size_after_rename
        return dedupe_by_size_after_rename(canonical, dest, log_emit=self.log.emit)

    def _hook(self, d):
        if self._cancel[0]:
            raise Exception("用户取消")
        status = d.get("status")
        if status == "downloading":
            if self._wd is not None:
                if self._wd.is_dead(self._active_key):
                    raise StalledDownloadError(
                        f"{int(self._wd.stall_seconds)} 秒无响应，已视为无法下载"
                    )
                self._wd.touch(self._active_key)
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            local = int(done * 100 / total) if total else 0
            local = max(0, min(99, local))
            # 多任务时把单任务进度映射到总进度段
            span = 100 / self._job_count
            pct = int(self._job_base * span + local * span / 100)
            pct = max(0, min(99, pct))
            speed = d.get("speed")
            eta = d.get("eta")
            parts = []
            if speed:
                try:
                    parts.append(f"{speed / 1024 / 1024:.1f} MB/s")
                except Exception:
                    pass
            if eta is not None:
                parts.append(f"ETA {eta}s")
            self.progress.emit(pct, "下载中 " + " · ".join(parts) if parts else "下载中…")
        elif status == "finished":
            self._last_path = d.get("filename") or self._last_path
            self.progress.emit(
                min(99, int((self._job_base + 1) * 100 / self._job_count) - 1),
                "合并 / 收尾…",
            )
            self.log.emit(f"片段完成：{os.path.basename(self._last_path or '')}", "info")

    def run(self):
        if not HAS_YTDLP:
            self.err.emit("未安装 yt-dlp，请执行：pip install -U yt-dlp")
            self.finished_clean.emit()
            return
        if not self.url:
            self.err.emit("没有可下载的链接")
            self.finished_clean.emit()
            return
        if not self.jobs:
            self.err.emit("没有可下载的项目（请勾选媒体卡）")
            self.finished_clean.emit()
            return
        save_dir = (self.save_dir or "").strip() or os.path.expanduser("~/Downloads")
        try:
            os.makedirs(save_dir, exist_ok=True)
        except Exception as e:
            self.err.emit(f"无法创建保存目录：{e}")
            self.finished_clean.emit()
            return

        ffmpeg = _which_exe("ffmpeg")
        if not ffmpeg:
            self.log.emit(
                "未检测到 ffmpeg（PATH）。多清晰度合并/转 mp3 可能失败；"
                "仅单文件流仍可下载。",
                "warn",
            )
        if not _which_exe("ffmpeg"):
            self.log.emit(
                "未检测到 ffmpeg — 高清 DASH 合并可能失败，建议 winget install Gyan.FFmpeg",
                "warn",
            )
        self.log.emit(f"保存到：{save_dir}", "info")

        # 复用解析策略；若无 profile（旧状态）则尽量安全降级
        profile = self.extract_profile or {}
        use_cookie = bool(profile.get("use_cookie"))
        ck = (profile.get("cookie_path") or self.cookie_path or "").strip()
        if use_cookie and not (ck and os.path.isfile(ck)):
            use_cookie = False
        ea = profile.get("extractor_args")
        if profile.get("name"):
            self.log.emit(f"复用解析策略：{profile['name']}", "info")
        else:
            # 无 profile：默认客户端（更易出高清自适应），有 Cookie 则带上
            use_cookie = bool(ck and os.path.isfile(ck))
            ea = None
            self.log.emit("无解析策略缓存，回退：默认客户端", "warn")

        title = self.video_meta.get("title") or "bilibili_video"
        vid = self.video_meta.get("id") or ""
        base_name = _safe_title_name(title, vid)

        last_ok_path = ""
        wd = self._wd = DownloadWatchdog()
        stall_msg = f"{int(wd.stall_seconds)} 秒无响应，已视为无法下载"
        try:
            for i, job in enumerate(self.jobs):
                if self._cancel[0]:
                    self.err.emit("已取消")
                    self.finished_clean.emit()
                    return
                self._job_base = i
                self._job_count = len(self.jobs)
                label = job.get("label") or f"任务{i+1}"
                fmt_str = job.get("format") or _FALLBACK_BEST[1]
                audio_only = bool(job.get("audio_only"))
                self._active_key = f"bili-{i}-{label}"
                wd.begin(self._active_key)
                self.log.emit(f"下载「{label}」· format={fmt_str}", "info")
                self.progress.emit(int(i * 100 / len(self.jobs)), f"开始：{label}")

                path = ""
                last_job_err = None

                # ── 路径 A：优先用解析阶段锁定的 format_id 直链 ──────────────
                # 旧逻辑只要 format 串含 "+" 就强制 yt-dlp 二次 extract，
                # 二次列表经常丢高清自适应、只剩 ~608x1080 一类低清 progressive，
                # 界面仍显示解析时的 1080p/1920p → 典型「标称高、文件糊」。
                # 现在：第一段若是具体 id 或 id+id 且缓存有 url，先直链下这些轨。
                picked = _pick_formats_for_selector(fmt_str, self.formats_full)

                if picked and not audio_only:
                    try:
                        mode = picked[0]
                        if mode == "single":
                            f = picked[1]
                            ext = f.get("ext") or "mp4"
                            canonical = os.path.join(save_dir, f"{base_name}.{ext}")
                            dest = _resolve_path(canonical, self._overwrite)
                            self.log.emit(
                                f"  直链下载 format_id={f.get('format_id')} "
                                f"（{_format_dim_text(f)}）…",
                                "info",
                            )

                            def _pcb(p, _i=i, _n=len(self.jobs)):
                                span = 100 / max(1, _n)
                                self.progress.emit(
                                    int(_i * span + p * span / 100),
                                    f"直链下载 {p}%",
                                )

                            path = _http_download(
                                f["url"], dest,
                                headers=f.get("http_headers") or {},
                                cancel_flag=self._cancel,
                                progress_cb=_pcb,
                                watchdog=wd, key=self._active_key,
                                stall_msg=stall_msg,
                            )
                            path = self._finalize_dest(path, canonical)
                        elif mode == "merge":
                            # 分轨：先下两个流，有 ffmpeg 再合并
                            vf, af = picked[1], picked[2]
                            vext = vf.get("ext") or "mp4"
                            aext = af.get("ext") or "m4a"
                            v_can = os.path.join(save_dir, f"{base_name}.v.{vext}")
                            a_can = os.path.join(save_dir, f"{base_name}.a.{aext}")
                            vpath = _resolve_path(v_can, self._overwrite)
                            apath = _resolve_path(a_can, self._overwrite)
                            self.log.emit(
                                f"  直链分轨下载：视频 {vf.get('format_id')} "
                                f"（{_format_dim_text(vf)}）+ 音频 {af.get('format_id')}…",
                                "info",
                            )
                            _http_download(
                                vf["url"], vpath,
                                headers=vf.get("http_headers") or {},
                                cancel_flag=self._cancel,
                                progress_cb=lambda p: self.progress.emit(
                                    int(i * 100 / len(self.jobs) + p * 0.4 / len(self.jobs)),
                                    f"视频 {p}%",
                                ),
                                watchdog=wd, key=self._active_key,
                                stall_msg=stall_msg,
                            )
                            _http_download(
                                af["url"], apath,
                                headers=af.get("http_headers") or {},
                                cancel_flag=self._cancel,
                                progress_cb=lambda p: self.progress.emit(
                                    int(i * 100 / len(self.jobs) + 40 / len(self.jobs) + p * 0.4 / len(self.jobs)),
                                    f"音频 {p}%",
                                ),
                                watchdog=wd, key=self._active_key,
                                stall_msg=stall_msg,
                            )
                            if ffmpeg:
                                out_can = os.path.join(save_dir, f"{base_name}.mp4")
                                out = _resolve_path(out_can, self._overwrite)
                                self.log.emit("  ffmpeg 合并…", "info")
                                cmd = [
                                    ffmpeg, "-y",
                                    "-i", vpath, "-i", apath,
                                    "-c", "copy", out,
                                ]
                                subprocess_ok = False
                                try:
                                    import subprocess
                                    r = subprocess.run(
                                        cmd, capture_output=True, timeout=300
                                    )
                                    subprocess_ok = r.returncode == 0 and os.path.isfile(out)
                                except Exception as e:
                                    self.log.emit(f"  ffmpeg 失败：{e}", "warn")
                                if subprocess_ok:
                                    path = self._finalize_dest(out, out_can)
                                    for p in (vpath, apath):
                                        try:
                                            os.remove(p)
                                        except Exception:
                                            pass
                                else:
                                    path = self._finalize_dest(vpath, v_can)
                                    self.log.emit("  合并失败，已保留分离文件", "warn")
                            else:
                                path = self._finalize_dest(vpath, v_can)
                                self.log.emit(
                                    "  无 ffmpeg：已保存分离的视频轨（高清）与音频轨；"
                                    "建议安装 ffmpeg 以自动合并为单个 mp4",
                                    "warn",
                                )
                    except Exception as e:
                        last_job_err = e
                        path = ""
                        self.log.emit(
                            f"  直链下载失败，回退 yt-dlp：{_strip_ansi(str(e))[:120]}",
                            "warn",
                        )

                # 音频卡：若有独立音频 format 直链也走直链
                if not path and audio_only and picked and picked[0] == "single":
                    try:
                        f = picked[1]
                        ext = f.get("ext") or "m4a"
                        canonical = os.path.join(save_dir, f"{base_name}.audio.{ext}")
                        dest = _resolve_path(canonical, self._overwrite)
                        self.log.emit(f"  直链下音频 format_id={f.get('format_id')}…", "info")
                        path = _http_download(
                            f["url"], dest,
                            headers=f.get("http_headers") or {},
                            cancel_flag=self._cancel,
                            progress_cb=lambda p: self.progress.emit(p, f"音频 {p}%"),
                            watchdog=wd, key=self._active_key,
                            stall_msg=stall_msg,
                        )
                        path = self._finalize_dest(path, canonical)
                    except Exception as e:
                        last_job_err = e
                        path = ""

                # ── 路径 B：yt-dlp 二次 extract（复用同一策略）──────────────
                # 仅当缓存无直链（DASH 分片 / 通配符选择器 / 直链失败）时使用。
                if not path:
                    if wd.is_dead(self._active_key):
                        raise StalledDownloadError(stall_msg)
                    ydl_stem = f"{base_name}.audio" if audio_only else base_name
                    ydl_stem, ydl_renamed = _ydl_numbered_stem(save_dir, ydl_stem)
                    if audio_only:
                        outtmpl = os.path.join(save_dir, f"{ydl_stem}.%(ext)s")
                    else:
                        outtmpl = os.path.join(save_dir, f"{ydl_stem}.%(ext)s")
                    ydl_canonical_stem = os.path.join(
                        save_dir, f"{base_name}.audio" if audio_only else base_name
                    )
                    if ydl_renamed:
                        self.log.emit(
                            f"同名已存在 → 改存为 {os.path.basename(ydl_stem)}.*，"
                            "完成后按大小判定是否真重复",
                            "blue",
                        )
                    # 优先完整 format 串（含锁定的 id+id）；失败再试横竖屏友好的自适应
                    attempt_formats = [fmt_str]
                    primary0 = (fmt_str or "").split("/")[0].strip()
                    if primary0 and primary0 not in ("best", "b"):
                        # 不要用裸 best 抢先降质；保留 res 优先的自适应
                        attempt_formats.append("bv*+ba/b/bestvideo*+bestaudio/best")

                    for fi, one_fmt in enumerate(attempt_formats):
                        if self._cancel[0]:
                            break
                        opts = _base_ydl_opts(
                            cookie_path=ck,
                            download=True,
                            use_cookie=use_cookie,
                            extractor_args=ea,
                        )
                        opts.update({
                            "outtmpl": outtmpl,
                            "format": one_fmt,
                            "overwrites": False,
                            # res=max(w,h)，竖屏 1080x1920 与横屏 1920x1080 同档优先
                            "format_sort": ["res", "tbr", "fps"],
                            "progress_hooks": [self._hook],
                            "retries": 3,
                            "fragment_retries": 3,
                            "concurrent_fragment_downloads": 4,
                            "restrictfilenames": False,
                            "windowsfilenames": True,
                            "ignore_no_formats_error": False,
                        })
                        if audio_only:
                            opts["postprocessors"] = [{
                                "key": "FFmpegExtractAudio",
                                "preferredcodec": "mp3",
                                "preferredquality": "192",
                            }]
                            opts["prefer_ffmpeg"] = True
                        else:
                            opts["merge_output_format"] = "mp4"
                        if ffmpeg:
                            opts["ffmpeg_location"] = os.path.dirname(ffmpeg)
                        elif not audio_only and ("+" in one_fmt or "bv" in one_fmt):
                            self.log.emit(
                                "  未检测到 ffmpeg：自适应高清可能无法合并，"
                                "将尽量选单文件流（画质可能偏低）",
                                "warn",
                            )
                        try:
                            with yt_dlp.YoutubeDL(opts) as ydl:
                                info = ydl.extract_info(self.url, download=True)
                            path = self._last_path
                            if info:
                                try:
                                    # 记录 yt-dlp 实际选中的分辨率，便于对照
                                    rw = info.get("width") or 0
                                    rh = info.get("height") or 0
                                    rid = info.get("format_id") or ""
                                    if rw or rh:
                                        self.log.emit(
                                            f"  yt-dlp 选定：{rid or '?'} · {rw}x{rh}",
                                            "info",
                                        )
                                    path = yt_dlp.YoutubeDL(opts).prepare_filename(info)
                                    if audio_only:
                                        base, _ = os.path.splitext(path)
                                        for ext in (".mp3", ".m4a", ".webm", ".opus"):
                                            if os.path.isfile(base + ext):
                                                path = base + ext
                                                break
                                except Exception:
                                    pass
                            if path and os.path.isfile(path):
                                if ydl_renamed:
                                    ext = os.path.splitext(path)[1]
                                    path = self._finalize_dest(
                                        path, ydl_canonical_stem + ext
                                    )
                                self.log.emit(f"  yt-dlp 下载成功（{one_fmt[:80]}）", "ok")
                                break
                        except Exception as e:
                            last_job_err = e
                            self.log.emit(
                                f"  yt-dlp 失败（{one_fmt[:60]}）："
                                f"{_strip_ansi(str(e))[:120]}",
                                "warn",
                            )
                            path = ""

                if self._cancel[0]:
                    self.err.emit("已取消")
                    self.finished_clean.emit()
                    return
                if path and os.path.isfile(path):
                    last_ok_path = path
                    self.log.emit(f"✓ {label} → {path}", "ok")
                else:
                    if last_job_err:
                        raise last_job_err
                    raise RuntimeError(f"下载未生成文件：{label}")

            self.progress.emit(100, "完成 ✓")
            self.ok.emit(last_ok_path or save_dir)
        except StalledDownloadError as e:
            self.err.emit(str(e))
        except Exception as e:
            if self._cancel[0] or "用户取消" in str(e):
                self.err.emit("已取消")
            else:
                self.err.emit(_friendly_ytdlp_error(str(e).strip() or repr(e)))
        finally:
            wd.stop()
            self.finished_clean.emit()


# ═══════════════════════════════════════════════════════════════════════════
#  UI
# ═══════════════════════════════════════════════════════════════════════════

class PageBilibili(QWidget):
    # 侧栏「视频下载」下 2px 进度线：active, 0–100（-1=解析灰线）, 颜色
    nav_progress = pyqtSignal(bool, int, str)

    def __init__(self):
        super().__init__()
        self._info = None
        self._parse_worker = None
        self._dl_worker = None
        self._cancel_flag = [False]
        self._auto_dl = False
        self._cards = []
        self._quality_options = []
        self._selected_quality_idx = 0
        self._quality_cards = []
        self._cookie_auto_tried = False   # 本会话是否已尝试自动加载 Cookie
        self._cookie_loaded_via = None    # 'auto' | 'manual' | None
        self._nav_busy = False  # 解析或下载中（侧栏进度线可见）
        self._tail_url_text = ""
        self._tail_saved_path = ""
        self._tail_started_at = 0.0

        self._last_download_ok = False  # 供 PageVideo 自动下载追踪结果

        theme.changed.connect(self.refresh_theme)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        # ══ 解析与下载（无圆角外框，融入 Tab 内容区）══════════════════════════
        gb_main = make_card("CardBilibiliParse", borderless=True)
        gv = QVBoxLayout(gb_main)
        gv.setSpacing(0)
        gv.setContentsMargins(0, max(0, CARD_TOP_GAP - 6), 0, CARD_BOTTOM_GAP)
        self._theme_titles = []
        self._func_cards = [gb_main]

        body_main = QWidget()
        body_main.setObjectName("BilibiliParseBody")
        body_main.setStyleSheet("#BilibiliParseBody{background:transparent;border:none;}"
                                "#Row2Left{background:transparent;}")
        gv_body = QVBoxLayout(body_main)
        gv_body.setContentsMargins(0, 0, 0, 0)
        gv_body.setSpacing(7)
        gv.addWidget(body_main, 1)

        # ── 第1块：Cookie 配置（由「关于」弹窗统一管理，页面整行隐藏）──
        self._conf_wrapper = QWidget()
        wrap_l = QVBoxLayout(self._conf_wrapper)
        wrap_l.setContentsMargins(0, 0, 0, 0)
        wrap_l.setSpacing(0)

        conf_row = QHBoxLayout()
        conf_row.setSpacing(8)

        ck_col = QVBoxLayout()
        ck_col.setSpacing(5)
        ck_head = QHBoxLayout()
        ck_head.setSpacing(8)
        lbl_ck = QLabel("Cookie 文件（可选，Netscape .txt）")
        lbl_ck.setObjectName("SecTitle")
        ck_head.addWidget(lbl_ck, 0)
        self.ck_status = _ElideLabel("")
        self.ck_status.setObjectName("StatusLbl")
        self.ck_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.ck_status.setStyleSheet(f"color:{tk('text_faint')}; font-size:12px;")
        ck_head.addWidget(self.ck_status, 1)
        ck_col.addLayout(ck_head)
        ck_row = QHBoxLayout()
        ck_row.setSpacing(6)
        self.btn_ck = QPushButton("选择文件")
        self.btn_ck.setObjectName("BtnSmall")
        self.btn_ck.setMinimumWidth(72)
        self.btn_ck.clicked.connect(self._pick_cookie)
        ck_row.addWidget(self.btn_ck)
        self.ck_path = QLineEdit()
        self.ck_path.setPlaceholderText("自动扫描下载目录…")
        self.ck_path.setReadOnly(True)
        self._ck_path_icon_action = apply_folder_path_edit(self.ck_path)
        self.ck_path.textChanged.connect(self._on_cookie_change)
        ck_row.addWidget(self.ck_path, 1)
        ck_col.addLayout(ck_row)
        conf_row.addLayout(ck_col, 60)

        conf_row.addStretch(40)

        wrap_l.addLayout(conf_row)
        gv_body.addWidget(self._conf_wrapper)
        self._conf_wrapper.setVisible(False)

        # ── Row1：链接输入 + 取消 ────────────────────────────────────────
        row1 = QHBoxLayout()
        row1.setSpacing(10)

        url_wrap = QWidget()
        url_wrap.setAttribute(Qt.WA_StyledBackground, True)
        url_wrap.setStyleSheet("background: transparent;")
        url_wrap_lay = QVBoxLayout(url_wrap)
        url_wrap_lay.setContentsMargins(0, 0, 0, 0)
        url_wrap_lay.setSpacing(0)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("粘贴 B站链接 / BV号 → 回车自动解析下载")
        self.url_edit.setFixedHeight(MEDIUM_BUTTON_H)
        self._url_edit_icon_action = apply_folder_path_edit(self.url_edit)
        self.url_edit.returnPressed.connect(lambda: self._start_flow(False))
        url_wrap_lay.addWidget(self.url_edit)
        self._dl_progress_line = DouyinProgressLine()
        url_wrap_lay.addWidget(self._dl_progress_line)
        row1.addWidget(url_wrap, 1)

        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("BtnCancel")
        self.btn_cancel.setFixedHeight(MEDIUM_BUTTON_H)
        self.btn_cancel.setFixedWidth(56)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(lambda: self._cancel_flag.__setitem__(0, True))
        row1.addWidget(self.btn_cancel, 0, Qt.AlignTop)
        gv_body.addLayout(row1)

        # ── Row2：状态提示（已无独立批处理按钮，统一由速存图文记录卡启动）──
        row2 = QHBoxLayout()
        row2.setSpacing(10)

        self.lbl_batch_hint = QLabel("粘贴链接后按回车开始解析")
        self.lbl_batch_hint.setFixedHeight(MEDIUM_BUTTON_H)
        self.lbl_batch_hint.setAlignment(Qt.AlignVCenter)
        row2.addWidget(self.lbl_batch_hint, 1)

        gv_body.addLayout(row2)

        self.progress = QProgressBar()
        self.progress.setMaximum(100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.progress.valueChanged.connect(self._on_progress_value)

        # 保存路径（不可见，仍被各方法引用）
        self.save_edit = QLineEdit(os.path.expanduser("~/Downloads").replace("\\", "/"))
        self._save_edit_icon_action = apply_folder_path_edit(self.save_edit)
        self.save_edit.setVisible(False)

        root.addWidget(gb_main)

        # ══ 媒体内容（对齐抖音：横向内容卡 + 全选/选中下载）══════════════════
        media_head = QHBoxLayout()
        self._sec_label_widget(media_head, "媒体内容（解析后显示）")
        media_head.addStretch(1)
        self.btn_all    = QPushButton("全选")
        self.btn_none   = QPushButton("全不选")
        self.btn_dl_sel = QPushButton("选中下载")
        self.btn_open_dir = QPushButton("打开目录")
        for b in (self.btn_all, self.btn_none, self.btn_dl_sel, self.btn_open_dir):
            b.setObjectName("BtnSmall")
            b.setStyleSheet("QPushButton{padding:2px 10px;} QPushButton:disabled{color:#555555;background:transparent;border-color:#333333;}")
            media_head.addWidget(b)
        for b in (self.btn_all, self.btn_none, self.btn_dl_sel):
            b.setEnabled(False)
        self.btn_dl_sel.setMinimumWidth(84)
        self.btn_open_dir.setMinimumWidth(84)
        self.btn_open_dir.setEnabled(True)
        self.btn_all.clicked.connect(self._select_all)
        self.btn_none.clicked.connect(self._deselect_all)
        self.btn_dl_sel.clicked.connect(self._download_selected)
        self.btn_open_dir.clicked.connect(self._open_save_dir)
        root.addLayout(media_head)

        self.card_scroll = QScrollArea()
        self.card_scroll.setWidgetResizable(True)
        self.card_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.card_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.card_scroll.setFixedHeight(MediaCard.CARD_H + 16)
        self.card_scroll.setFrameShape(QFrame.NoFrame)
        self.card_scroll.setObjectName("BiliCardScroll")
        self.card_scroll.viewport().setAutoFillBackground(False)
        self.card_scroll.setStyleSheet(
            "QScrollArea#BiliCardScroll{background:transparent;border:none;}"
            "QScrollArea#BiliCardScroll > QWidget > QWidget{background:transparent;}"
        )
        card_inner = QWidget()
        card_inner.setAutoFillBackground(False)
        self._card_layout = QHBoxLayout(card_inner)
        self._card_layout.setContentsMargins(4, 4, 4, 4)
        self._card_layout.setSpacing(8)
        self.card_scroll.setWidget(card_inner)
        root.addWidget(self.card_scroll)

        self._empty_card = None
        self._show_empty_card()

        # ══ 运行日志（无圆角外框）═════════════════════════════════════════════
        card_log = make_card("CardBilibiliLog", borderless=True)
        log_lay = QVBoxLayout(card_log)
        log_lay.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        log_lay.setSpacing(0)
        title_log = install_card_title(card_log, log_lay, "运行日志")
        self._theme_titles.append(title_log)
        self._func_cards.append(card_log)

        head = title_log.parentWidget()
        head_l = head.layout()
        if head_l is not None:
            head_l.removeWidget(title_log)
            title_row = QHBoxLayout()
            title_row.setContentsMargins(0, 0, 0, 0)
            title_row.setSpacing(6)
            title_row.addWidget(title_log, 0, Qt.AlignVCenter)
            title_row.addStretch(1)
            self.btn_clear_log = apply_mini_button(QPushButton("清除记录"))
            self.btn_clear_log.clicked.connect(lambda: self.log_box.clear())
            title_row.addWidget(self.btn_clear_log, 0, Qt.AlignVCenter)
            head_l.addLayout(title_row)

        self.log_box = QTextEdit()
        self.log_box.setObjectName("BilibiliLogBox")
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(72)
        self.log_box.setFrameShape(QFrame.NoFrame)
        apply_simple_record(self.log_box)
        log_lay.addWidget(self.log_box, 1)
        root.addWidget(card_log, 1)

        if not HAS_YTDLP:
            QTimer.singleShot(0, self._warn_no_ytdlp)

    # ── 样式 / 主题 ───────────────────────────────────────────────────────────

    def refresh_theme(self, *_):
        if hasattr(self, "dir_hint"):
            self.dir_hint.setStyleSheet(f"color:{tk('text_faint')}; font-size:12px;")
        if hasattr(self, "ck_path"):
            restyle_folder_path_edit(self.ck_path, getattr(self, "_ck_path_icon_action", None))
        if hasattr(self, "save_edit"):
            restyle_folder_path_edit(self.save_edit, getattr(self, "_save_edit_icon_action", None))
        if hasattr(self, "url_edit"):
            restyle_folder_path_edit(self.url_edit, getattr(self, "_url_edit_icon_action", None))
        for c in getattr(self, "_cards", []):
            if hasattr(c, "refresh_theme"):
                c.refresh_theme()
        for lbl in self._theme_titles:
            restyle_card_title(lbl)
        for frame in getattr(self, "_func_cards", []):
            restyle_card_frame(frame)
        # Cookie 按钮绿色态要跟主题重刷
        path = (self.ck_path.text() or "").strip() if hasattr(self, "ck_path") else ""
        self._set_cookie_btn_loaded(bool(path and os.path.isfile(path)))

    # ── 日志 / 进度 ───────────────────────────────────────────────────────────

    def _sec_label_widget(self, parent_layout, text):
        lbl = QLabel(text)
        lbl.setObjectName("SecTitle")
        parent_layout.addWidget(lbl)

    def _log(self, msg, level="info"):
        colors = {
            "ok": tk("ok"), "err": tk("err"), "warn": tk("warn"),
            "info": tk("text_mut"), "blue": tk("info"),
        }
        color = colors.get(level, tk("text_mut"))
        ts = time.strftime("%H:%M:%S")
        sb = self.log_box.verticalScrollBar()
        follow = sb.value() >= sb.maximum() - 8
        self.log_box.append(
            f'<span style="color:{tk("text_faint")}">[{ts}]</span> '
            f'<span style="color:{color}">{msg}</span>'
        )
        if follow:
            self._scroll_log_bottom()

    def _scroll_log_bottom(self):
        self.log_box.moveCursor(QTextCursor.End)
        self.log_box.ensureCursorVisible()
        sb = self.log_box.verticalScrollBar()
        sb.setValue(sb.maximum())
        QTimer.singleShot(0, lambda: sb.setValue(sb.maximum()))

    def _project_dir_name(self) -> str:
        """项目名：与落盘文件名前缀一致（视频标题）。"""
        info = self._info or {}
        title = (info.get("title") or "").strip()
        if not title:
            return ""
        try:
            return _safe_title_name(title, info.get("id") or "")
        except Exception:
            name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", title).strip(" ._")
            return name[:56] + ("…" if len(name) > 56 else "")

    def _with_project(self, text: str) -> str:
        name = self._project_dir_name()
        if not name:
            return text or ""
        if len(name) > 56:
            name = name[:55].rstrip(" .") + "…"
        base = (text or "").rstrip()
        if not base:
            return name
        if name in base or base.endswith(name):
            return base
        return f"{base} · {name}"

    def _set_status(self, text, color=None):
        """状态提示行：下载相关必须醒目，有色时加粗。

        过程中/完成后自动追加项目名（视频标题/文件名主干）。
        """
        body = text or ""
        if body and any(
            k in body
            for k in ("下载中", "完成：", "准备下载", "失败", "% ·", "%·")
        ):
            body = self._with_project(body)
        try:
            if hasattr(self, "lbl_batch_hint"):
                self.lbl_batch_hint.setText(body)
                col = color or tk("text_mut")
                weight = "700" if color else "600"
                self.lbl_batch_hint.setStyleSheet(
                    f"color:{col}; font-weight:{weight}; font-size:13px;"
                )
        except Exception:
            pass

    @staticmethod
    def _progress_gradient_color(pct: int) -> str:
        pct = max(0, min(100, pct))
        red, yellow, green = (239, 68, 68), (234, 179, 8), (34, 197, 94)
        if pct <= 50:
            c0, c1, t = red, yellow, pct / 50
        else:
            c0, c1, t = yellow, green, (pct - 50) / 50
        r = round(c0[0] + (c1[0] - c0[0]) * t)
        g = round(c0[1] + (c1[1] - c0[1]) * t)
        b = round(c0[2] + (c1[2] - c0[2]) * t)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _on_progress_value(self, value):
        # 空闲时忽略 progress 控件的残留 valueChanged，避免把已隐藏的简易线又画出来
        if not getattr(self, "_nav_busy", False):
            return
        color = self._progress_gradient_color(value)
        try:
            self._dl_progress_line.set_progress(value, color)
        except Exception:
            pass
        if not (self._parse_worker and self._parse_worker.isRunning()):
            self._emit_nav_progress(True, int(value), mode="progress")

    def _emit_nav_progress(self, active: bool, pct: int = 0, mode: str = "progress"):
        """页内简易进度线 + 侧栏进度线统一入口。

        显示：busy=解析灰线 / progress=下载彩色。
        隐藏：必须 active=False（完成/失败/取消/跳过/无任务）。
        """
        self._nav_busy = bool(active)
        if not active:
            try:
                self.nav_progress.emit(False, 0, "")
            except Exception:
                pass
            try:
                self._dl_progress_line.hide_line()
            except Exception:
                pass
            return
        if mode == "busy":
            try:
                self.nav_progress.emit(True, -1, "#94a3b8")
            except Exception:
                pass
            try:
                self._dl_progress_line.set_busy(True)
            except Exception:
                pass
            return
        pct = max(0, min(100, int(pct or 0)))
        color = self._progress_gradient_color(pct)
        try:
            self.nav_progress.emit(True, pct, color)
        except Exception:
            pass
        try:
            self._dl_progress_line.set_progress(pct, color)
        except Exception:
            pass

    # ── Cookie / 目录 ─────────────────────────────────────────────────────────

    def showEvent(self, event):
        """本会话第一次显示本页时：自动在下载目录扫描并加载 B站 Cookie。"""
        super().showEvent(event)
        if self._cookie_auto_tried:
            return
        self._cookie_auto_tried = True
        QTimer.singleShot(0, self._auto_load_cookie)

    def _set_cookie_btn_loaded(self, loaded: bool):
        """加载成功 → 绿色「已加载」；否则恢复「选择文件」。仍可点击重选。"""
        btn = getattr(self, "btn_ck", None)
        if btn is None:
            return
        if loaded:
            btn.setText("已加载")
            btn.setStyleSheet(
                f"QPushButton{{background:{tk('ok')}; color:#ffffff;"
                f"border:1px solid {tk('ok')}; border-radius:6px;"
                f"padding:4px 12px; font-weight:600;}}"
                f"QPushButton:hover{{background:{tk('ok')}; color:#ffffff;}}"
            )
        else:
            btn.setText("选择文件")
            btn.setStyleSheet("")
            st = btn.style()
            if st is not None:
                st.unpolish(btn)
                st.polish(btn)
            btn.update()

    def _auto_load_cookie(self):
        """扫描下载目录，优先加载 www.bilibili.com_cookies.txt。"""
        cur = (self.ck_path.text() or "").strip()
        if cur and os.path.isfile(cur):
            # 已有有效路径（例如用户刚选手动），只刷新按钮态
            self._on_cookie_change(cur)
            return

        dirs = []
        if hasattr(self, "save_edit"):
            save_dir = (self.save_edit.text() or "").strip()
            if save_dir:
                dirs.append(save_dir)
        for d in _default_download_dirs():
            dirs.append(d)

        path = find_bilibili_cookie_file(dirs)
        if path:
            self._cookie_loaded_via = "auto"
            self.ck_path.setText(path.replace("\\", "/"))  # 触发 _on_cookie_change
            name = os.path.basename(path)
            self._log(
                f"✓ 已自动从下载目录加载 Cookie「{name}」，"
                f"无需再点「选择文件」（路径：{path}）",
                "ok",
            )
            self._set_status("Cookie 已自动加载 ✓", "#22c55e")
        else:
            self._cookie_loaded_via = None
            self._set_cookie_btn_loaded(False)
            self._log(
                "下载目录未找到 B站 Cookie"
                "（优先 www.bilibili.com_cookies.txt，Netscape .txt），"
                "请点「选择文件」手动指定；公开视频也可不填",
                "warn",
            )

    def _warn_no_ytdlp(self):
        self._log("未检测到 yt-dlp，B站下载不可用。请执行：pip install -U yt-dlp", "err")
        self._set_status("缺少 yt-dlp", "#ef4444")

    def _pick_cookie(self):
        start = (self.ck_path.text() or "").strip()
        if start and os.path.isfile(start):
            start = os.path.dirname(start)
        elif self.save_edit.text().strip():
            start = self.save_edit.text().strip()
        else:
            dd = _default_download_dirs()
            start = dd[0] if dd else os.path.expanduser("~/Downloads")
        p, _ = QFileDialog.getOpenFileName(
            self, "选择 cookies.txt", start,
            "Cookie 文件 (*.txt);;所有文件 (*.*)",
        )
        if p:
            self._cookie_loaded_via = "manual"
            self.ck_path.setText(p.replace("\\", "/"))
            self._log(f"✓ 已手动加载 Cookie：{p}", "ok")

    def _on_cookie_change(self, path):
        path = (path or "").strip()
        if not path or not os.path.isfile(path):
            self.ck_status.setFullText("")
            self.ck_status.setStyleSheet(f"color:{tk('text_faint')}; font-size:12px;")
            self._set_cookie_btn_loaded(False)
            return

        via_auto = getattr(self, "_cookie_loaded_via", None) == "auto"
        try:
            text = open(path, encoding="utf-8", errors="ignore").read()
        except Exception as e:
            self.ck_status.setFullText(f"❌ 读取失败: {e}")
            self.ck_status.setStyleSheet(f"color:{tk('err')}; font-size:12px;")
            self._set_cookie_btn_loaded(False)
            return

        low = text.lower()
        has_bili = ("bilibili.com" in low) or ("biliapi" in low) or ("b23.tv" in low)
        lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
        count = len(lines)
        strong = any(
            k in text
            for k in (
                "SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5",
            )
        )

        if not has_bili:
            self.ck_status.setFullText("⚠ 文件中未见 bilibili.com / SESSDATA")
            self.ck_status.setStyleSheet(f"color:{tk('warn')}; font-size:12px;")
            self._set_cookie_btn_loaded(False)
            return

        if strong:
            if via_auto:
                msg = f"✅ 已自动加载（{count} 行，含登录态）· 无需再点「选择文件」"
            else:
                msg = f"✅ Cookie 有效（{count} 行，含登录态）"
            self.ck_status.setStyleSheet(f"color:{tk('ok')}; font-size:12px;")
            self._set_cookie_btn_loaded(True)
        elif count > 0:
            prefix = "已自动加载但" if via_auto else ""
            msg = f"⚠ {prefix}可能缺少登录字段（SESSDATA 等）"
            self.ck_status.setStyleSheet(f"color:{tk('warn')}; font-size:12px;")
            # 文件有效仍算已加载（绿按钮），缺字段在状态里提示
            self._set_cookie_btn_loaded(True)
        else:
            msg = "⚠ Cookie 文件为空"
            self.ck_status.setStyleSheet(f"color:{tk('warn')}; font-size:12px;")
            self._set_cookie_btn_loaded(False)
        # setFullText：窄窗省略显示，完整文案由 setFullText 持有
        self.ck_status.setFullText(msg)

    def _choose_dir(self):
        d = QFileDialog.getExistingDirectory(
            self, "选择保存目录", self.save_edit.text().strip() or os.path.expanduser("~")
        )
        if d:
            self.save_edit.setText(d.replace("\\", "/"))

    def _show_help(self):
        """「安装」按钮：用白话说明本机缺什么、干什么用、怎么装。"""
        ffmpeg_ok = bool(_which_exe("ffmpeg"))

        if HAS_YTDLP:
            ytdlp_status = f"已安装（版本 {YTDLP_VER}）"
        else:
            ytdlp_status = "未安装（没有它就无法解析/下载 B站）"
        ytdlp_how = "pip install -U yt-dlp"

        if HAS_YTDLP_EJS:
            ejs_status = "已安装"
        else:
            ejs_status = "未安装"
        ejs_how = "pip install -U yt-dlp-ejs"

        if ffmpeg_ok:
            ff_status = "已找到（可合并高清画面+声音、转 mp3）"
        else:
            ff_status = "未找到（仍可下，但高清合并/转 mp3 可能失败）"
        ff_how = "winget install Gyan.FFmpeg\n或 https://ffmpeg.org 安装并加入 PATH"

        text = (
            f"【本机环境检测】\n"
            f"yt-dlp：{ytdlp_status}\n"
            f"  安装：{ytdlp_how}\n\n"
            f"yt-dlp-ejs：{ejs_status}\n"
            f"  安装：{ejs_how}\n\n"
            f"ffmpeg：{ff_status}\n"
            f"  安装：{ff_how}\n\n"
            "【使用方法】\n"
            "1. 粘贴 B站链接 / BV号 → 回车自动解析下载\n"
            "2. 选画质 → 自动下载到保存位置\n\n"
            "【Cookie 说明】\n"
            "需要大会员/高画质时：Chrome 装扩展 Get cookies.txt LOCALLY\n"
            "→ 登录 bilibili.com → Export 保存到下载目录，程序会自动加载"
        )
        try:
            from styles.style_all import message_box_info
            message_box_info(self, "B站 · 使用说明", text)
        except Exception:
            QMessageBox.information(self, "B站 · 使用说明", text)

    # ── 画质格式卡 ──────────────────────────────────────────────────────────────

    def _reset_quality_cards(self):
        self._quality_options = []
        self._selected_quality_idx = 0
        self._show_empty_card()

    def _apply_quality_cards(self, options: list):
        """options: [(label, format_str, is_audio), ...]，首项为最佳画质。

        每个选项就是一张独立内容卡（画质 A/B/C + 仅音频 = 有几项就几张卡），
        不再额外叠加「视频/音频轨」预览卡——那两张卡片本质是同一个视频的
        另一种描述，和画质卡拼在一起显示会让卡片数量对不上、产生冗余。
        """
        self._quality_options = options or [_FALLBACK_BEST]
        self._selected_quality_idx = 0
        self._clear_card_layout()
        self._quality_cards.clear()
        self.card_scroll.setFixedHeight(MediaCard.CARD_H + 16)

        thumb_url = (self._info or {}).get("thumbnail") or ""

        # 画质/音频选择卡（外观与媒体内容卡完全一致：无边框、透明底；
        # 具体格式 ID 等参数不再单独铺一行文字，改放进提示气泡）
        for i, (label, fmt_str, is_audio) in enumerate(self._quality_options):
            item = MediaItem(
                kind="audio" if is_audio else "video",
                label=label,
                url="",
                ext="m4a" if is_audio else "mp4",
                thumb_url=thumb_url,
                default_checked=(i == 0),
            )
            card = MediaCard(item)
            # 画质卡是单选（互斥），勾选框只用来展示「已选中」状态，
            # 不直接接收点击，避免和整卡点击的互斥选择逻辑打架
            card.chk.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            hint = f"格式：{fmt_str or '自动'}"
            hint += "\n仅音频" if is_audio else "\n视频＋音频"
            idx = i
            card.mousePressEvent = lambda e, idx=idx: self._on_quality_card_clicked(idx)
            self._card_layout.addWidget(card)
            self._quality_cards.append(card)
        self._card_layout.addStretch(1)

    def _current_quality(self):
        if not self._quality_options:
            return _FALLBACK_BEST
        idx = min(self._selected_quality_idx, len(self._quality_options) - 1)
        label, fmt_str, is_audio = self._quality_options[idx]
        return label, fmt_str, bool(is_audio)

    def _on_quality_card_clicked(self, idx: int):
        self._selected_quality_idx = idx
        for i, card in enumerate(self._quality_cards):
            card.chk.blockSignals(True)
            card.chk.setChecked(i == idx)
            card.chk.blockSignals(False)

    # ── 媒体内容卡 ────────────────────────────────────────────────────────────

    def _make_empty_card(self):
        card = QFrame()
        card.setObjectName("EmptyCard")
        card.setFixedSize(MediaCard.THUMB_W, MediaCard.CARD_H)
        card.setStyleSheet("QFrame#EmptyCard{background:transparent;border:1px solid #868686;border-radius:6px;}")
        v = QVBoxLayout(card)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        lbl = QLabel("粘贴 B站链接 →\n回车自动解析")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(
            f"color:{tk('text_faint')};font-size:12px;"
            f"background:transparent;border:none;"
        )
        v.addWidget(lbl)
        return card

    def _clear_card_layout(self):
        while self._card_layout.count():
            it = self._card_layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def _show_empty_card(self):
        for c in self._cards:
            c.deleteLater()
        self._cards.clear()
        self._clear_card_layout()
        self._empty_card = self._make_empty_card()
        self._card_layout.addWidget(self._empty_card)
        self._card_layout.addStretch(1)

    def _clear_cards(self):
        self.btn_all.setEnabled(False)
        self.btn_none.setEnabled(False)
        self.btn_dl_sel.setEnabled(False)
        self._show_empty_card()

    def _fill_cards(self, specs: list):
        for c in self._cards:
            c.deleteLater()
        self._cards.clear()
        self._clear_card_layout()
        self._empty_card = None

        for spec in specs or []:
            item = MediaItem(
                kind=spec.get("kind") or "video",
                label=spec.get("label") or "媒体",
                url=spec.get("url") or "",
                ext=spec.get("ext") or "mp4",
                thumb_url=spec.get("thumb_url") or "",
                default_checked=bool(spec.get("default_checked", True)),
            )
            card = MediaCard(item)
            self._cards.append(card)
            self._card_layout.addWidget(card)
        self._card_layout.addStretch(1)

        show = bool(self._cards)
        self.btn_all.setEnabled(show)
        self.btn_none.setEnabled(show)
        self.btn_dl_sel.setEnabled(show)
        self._log(f"显示 {len(self._cards)} 个媒体项", "ok")

    def _select_all(self):
        for c in self._cards:
            c.chk.setChecked(True)

    def _deselect_all(self):
        for c in self._cards:
            c.chk.setChecked(False)

    def _jobs_from_checked(self) -> list:
        """勾选的媒体卡 → 下载任务列表。"""
        q_label, q_fmt, q_audio = self._current_quality()
        # 若画质列表里有「仅音频」项，优先用其 format_id
        audio_fmt = _FALLBACK_AUDIO[1]
        if self._quality_options:
            for label, fmt_str, is_audio in self._quality_options:
                if is_audio and fmt_str:
                    audio_fmt = fmt_str
                    break
        jobs = []
        for c in self._cards:
            if not c.is_checked():
                continue
            kind = getattr(c.item, "kind", "video")
            if kind == "audio":
                jobs.append({
                    "label": c.item.label or "音频",
                    "format": audio_fmt,
                    "audio_only": True,
                })
            else:
                # 若用户在下拉框里直接选了「仅音频」，视频卡也按音频下
                jobs.append({
                    "label": f"{c.item.label or '视频'} · {q_label}",
                    "format": q_fmt,
                    "audio_only": q_audio,
                })
        return jobs

    # ── 主流程 ────────────────────────────────────────────────────────────────

    def _clipboard_raw(self) -> str:
        cb = QApplication.clipboard()
        if cb is None:
            return ""
        return (cb.text() or "").strip()

    def _clipboard_url(self) -> str:
        text = self._clipboard_raw()
        if not text:
            return ""
        try:
            extract_bilibili_url(text)
            return text
        except Exception:
            return ""

    @staticmethod
    def _looks_like_douyin(text: str) -> bool:
        t = (text or "").lower()
        return (
            "douyin.com" in t
            or "tiktok.com" in t
            or "v.douyin.com" in t
            or "vm.tiktok.com" in t
        )

    @staticmethod
    def _looks_like_youtube(text: str) -> bool:
        t = (text or "").lower()
        return (
            "youtube.com" in t
            or "youtu.be" in t
            or "music.youtube.com" in t
        )

    def _find_page_video(self):
        w = self.parent()
        while w is not None:
            if w.__class__.__name__ == "PageVideo":
                return w
            w = w.parent()
        return None

    def _start_flow(self, use_clipboard=True):
        if not HAS_YTDLP:
            self._warn_no_ytdlp()
            return
        text = ""
        raw_clip = self._clipboard_raw() if use_clipboard else ""
        if use_clipboard:
            text = self._clipboard_url()
            if text:
                self.url_edit.setText(text)
                self._log("已从剪贴板读取链接", "ok")
        if not text:
            text = self.url_edit.text().strip()

        probe = text or raw_clip

        def _is_bili(s: str) -> bool:
            try:
                extract_bilibili_url(s)
                return True
            except Exception:
                return False

        # 剪贴板/输入框是抖音/YouTube：自动切到对应分页并继续
        if probe and self._looks_like_douyin(probe) and not _is_bili(probe):
            handoff = text or raw_clip
            self._log(
                "检测到抖音/TikTok 链接，已自动切换到「抖音」分页并开始解析…",
                "ok",
            )
            pv = self._find_page_video()
            if pv is not None and hasattr(pv, "handoff_to_douyin"):
                pv.handoff_to_douyin(handoff, auto_start=True)
            else:
                self._log("请手动点上方 Tab「抖音」后再粘贴解析", "warn")
            return

        # YouTube 链接：切到 YouTube 分页
        if probe and self._looks_like_youtube(probe) and not _is_bili(probe):
            handoff = text or raw_clip
            self._log(
                "检测到 YouTube 链接，已自动切换到「YouTube」分页并开始解析…",
                "ok",
            )
            pv = self._find_page_video()
            if pv is not None and hasattr(pv, "handoff_to_youtube"):
                pv.handoff_to_youtube(handoff, auto_start=True)
            else:
                self._log("请手动点上方 Tab「YouTube」后再粘贴解析", "warn")
            return

        if not text:
            self._log("剪贴板里没有 B站链接，输入框也是空的", "warn")
            self._set_status("没有链接", "#f97316")
            return
        self._auto_dl = True
        self._parse(text)

    def _parse(self, url_text=None):
        if self._parse_worker and self._parse_worker.isRunning():
            self._log("正在解析中，请稍候…", "warn")
            return
        if self._dl_worker and self._dl_worker.isRunning():
            self._log("正在下载中，请先等待或取消", "warn")
            return
        if not isinstance(url_text, str) or not url_text.strip():
            url_text = self.url_edit.text()
        url_text = url_text.strip()
        if not url_text:
            self._log("请先粘贴视频链接", "warn")
            return

        self.btn_dl_sel.setEnabled(False)
        self.progress.setValue(0)
        self._set_status(batch_progress_prefix(self) + "解析中…", "#f97316")
        self._log("开始解析…")
        self._reset_quality_cards()
        self._clear_cards()
        # 侧栏：解析一开始就出灰色整线
        self._emit_nav_progress(True, mode="busy")

        self._parse_worker = YtParseWorker(url_text, self.ck_path.text().strip())
        self._parse_worker.ok.connect(self._on_parse_ok)
        self._parse_worker.err.connect(self._on_parse_err)
        self._parse_worker.log.connect(self._log)
        self._parse_worker.start()

    def _on_parse_ok(self, info: dict):
        self._info = info
        self.btn_dl_sel.setEnabled(True)
        self._set_status("解析成功 ✓", "#22c55e")

        # 画质：解析后填充，默认最佳
        opts = info.get("quality_options") or build_quality_options(info)
        self._apply_quality_cards(opts)

        if self._auto_dl:
            self._auto_dl = False
            q_label, q_fmt, q_audio = self._current_quality()
            jobs = [{"label": q_label, "format": q_fmt, "audio_only": q_audio}]
            self._log("自动开始下载…", "ok")
            self._start_download(jobs)
        else:
            self._emit_nav_progress(False)

    def _on_parse_err(self, msg: str):
        self._last_download_ok = False
        self._auto_dl = False
        self.btn_dl_sel.setEnabled(True)
        self._set_status("解析失败 ✗", "#ef4444")
        self._reset_quality_cards()
        self._log("━" * 40, "err")
        for line in (msg or "").split("\n"):
            if line.strip():
                self._log(f"  {line.strip()}", "err")
        self._log("━" * 40, "err")
        self._emit_nav_progress(False)


    def _download_selected(self):
        q_label, q_fmt, q_audio = self._current_quality()
        jobs = [{"label": q_label, "format": q_fmt, "audio_only": q_audio}]
        self._start_download(jobs)

    def _open_save_dir(self):
        """打开当前保存目录，方便核对是否下到了文件。"""
        path = ""
        try:
            path = (self.save_edit.text() or "").strip()
        except Exception:
            path = ""
        if not path:
            path = os.path.expanduser("~/Downloads")
        try:
            if not os.path.isdir(path):
                os.makedirs(path, exist_ok=True)
        except Exception:
            pass
        try:
            if os.path.isdir(path):
                os.startfile(path)
            else:
                self._log(f"目录不存在：{path}", "warn")
        except Exception as e:
            try:
                self._log(f"打开目录失败：{e}", "err")
            except Exception:
                pass

    def _collect_bilibili_conflicts(self, save_dir: str) -> list:
        """按标题/视频 ID 匹配目录里可能被覆盖的文件。"""
        from utils.download_confirm import (
            list_files_containing,
            list_files_with_prefix,
            existing_files,
        )
        info = self._info or {}
        title = info.get("title") or "bilibili_video"
        vid = info.get("id") or ""
        base = _safe_title_name(title, vid)
        found = []
        found.extend(list_files_with_prefix(save_dir, base))
        # yt-dlp 模板：title [id].ext，id 段更稳
        if vid:
            found.extend(list_files_containing(save_dir, f"[{vid}]"))
        return existing_files(found)

    def _start_download(self, jobs=None):
        if not self._info:
            self._log("请先解析视频", "warn")
            self._emit_nav_progress(False)
            return
        if self._dl_worker and self._dl_worker.isRunning():
            return
        jobs = list(jobs or self._jobs_from_checked())
        if not jobs:
            self._log("请至少勾选一项媒体后再下载", "warn")
            self._emit_nav_progress(False)
            return

        save_dir = (self.save_edit.text() or "").strip()
        if not save_dir:
            save_dir = os.path.expanduser("~/Downloads")
            self.save_edit.setText(save_dir.replace("\\", "/"))

        # 与抖音相同：不弹窗。同名先加序号下载，下完按大小判定真重复。
        overwrite = False
        try:
            conflicts = self._collect_bilibili_conflicts(save_dir)
            if conflicts:
                show = [os.path.basename(p) for p in conflicts[:4]]
                more = (
                    f" 等共 {len(conflicts)} 个"
                    if len(conflicts) > 4
                    else f"（{len(conflicts)} 个）"
                )
                self._log(
                    f"检测到同名文件 {'、'.join(show)}{more}："
                    f"将加序号下载，完成后按大小判定是否真重复",
                    "blue",
                )
        except Exception as e:
            self._log(f"同名预检异常（已忽略）：{e}", "warn")

        self._tail_url_text = self.url_edit.text().strip()
        self._tail_saved_path = ""
        self._tail_started_at = time.time()
        self._dl_jobs_n = len(jobs)
        self._dl_jobs_labels = [
            str((j or {}).get("label") or "").strip() for j in jobs
        ]

        self._cancel_flag[0] = False
        self.btn_dl_sel.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setValue(0)
        self._set_status("准备下载…", self._progress_gradient_color(0))
        self._emit_nav_progress(True, 0, mode="progress")

        url = self._info.get("webpage_url") or self.url_edit.text().strip()
        self._dl_worker = YtDownloadWorker(
            url=url,
            save_dir=save_dir,
            jobs=jobs,
            cookie_path=self.ck_path.text().strip(),
            extract_profile=self._info.get("extract_profile") or {},
            formats_full=self._info.get("formats") or [],
            video_meta={
                "title": self._info.get("title") or "",
                "id": self._info.get("id") or "",
            },
            cancel_flag=self._cancel_flag,
            overwrite=overwrite,
        )
        self._dl_worker.progress.connect(self._on_dl_progress)
        self._dl_worker.ok.connect(self._on_dl_ok)
        self._dl_worker.err.connect(self._on_dl_err)
        self._dl_worker.log.connect(self._log)
        self._dl_worker.finished_clean.connect(self._on_dl_finished)
        self._dl_worker.start()

    def _on_dl_progress(self, pct: int, text: str):
        self.progress.setValue(pct)  # 会触发 _on_progress_value → 侧栏同步
        try:
            pct = max(0, min(100, int(pct)))
        except (TypeError, ValueError):
            pct = 0
        # 状态行始终带百分比 + 红→黄→绿，便于一眼判断进度
        body = (text or "下载中…").strip()
        if not body.startswith(f"{pct}%"):
            body = f"{pct}% · {body}"
        self._set_status(body, self._progress_gradient_color(pct))
        self._emit_nav_progress(True, pct, mode="progress")

    def _on_dl_ok(self, path: str):
        self._last_download_ok = True
        self._last_batch_note = "成功"
        self.progress.setValue(100)
        n = int(getattr(self, "_dl_jobs_n", 0) or 0) or 1
        labels = [x for x in (getattr(self, "_dl_jobs_labels", None) or []) if x]
        label_hint = labels[0] if len(labels) == 1 else (f"{n} 项" if n > 1 else "")
        name = ""
        try:
            if path and os.path.isfile(path):
                name = os.path.basename(path)
        except Exception:
            name = ""
        title = ((self._info or {}).get("title") or "").strip()
        if name:
            short = name if len(name) <= 36 else (name[:33] + "…")
            summary = f"完成：共 {n} 项 · {short}"
        elif label_hint and title:
            t = title if len(title) <= 18 else (title[:16] + "…")
            summary = f"完成：共 {n} 项 · {label_hint} · {t}"
        elif title:
            t = title if len(title) <= 28 else (title[:26] + "…")
            summary = f"完成：共 {n} 项 · {t}"
        else:
            summary = f"完成：共 {n} 项 · 已保存"
        if len(summary) > 56:
            summary = summary[:54] + "…"
        self._set_status(summary, "#22c55e")
        self._emit_nav_progress(True, 100, mode="progress")
        if not in_batch_download(self):
            show_cursor_toast("下载", "完成", accent="ok")
        self._tail_saved_path = path or ""
        # TODO: 尾巴功能暂时屏蔽，避免阻止用户选择不同分辨率二次下载
        # QTimer.singleShot(1000, self._clear_url_after_download_tail)
        if path and os.path.isfile(path):
            self._log(f"✓ {summary} · {path}", "ok")
        else:
            self._log(f"✓ {summary} · 目录：{path}", "ok")

    def _report_batch_item_done(self):
        """自动下载：本条结束显式上报（校验成功才删记录行）。"""
        w = self
        try:
            while w is not None:
                if hasattr(w, "notify_batch_item_done") and getattr(w, "_batch_mode", False):
                    w.notify_batch_item_done(
                        bool(getattr(self, "_last_download_ok", False)),
                        note=(getattr(self, "_last_batch_note", None) or ""),
                        source="bilibili",
                    )
                    return
                w = w.parent()
        except Exception:
            pass

    def _clear_url_after_download_tail(self):
        path = getattr(self, "_tail_saved_path", "") or ""
        started_at = float(getattr(self, "_tail_started_at", 0.0) or 0.0)
        ok = False
        try:
            if os.path.isfile(path):
                ok = os.path.getsize(path) > 0
            elif os.path.isdir(path):
                for name in os.listdir(path):
                    p = os.path.join(path, name)
                    if os.path.isfile(p) and os.path.getsize(p) > 0 and os.path.getmtime(p) >= started_at:
                        ok = True
                        break
        except Exception:
            ok = False
        if ok and self.url_edit.text().strip() == getattr(self, "_tail_url_text", ""):
            self.url_edit.clear()
            self._tail_url_text = ""
            self._tail_saved_path = ""
            self._tail_started_at = 0.0
            self._log("下载尾巴：已确认文件存在，清空当前链接，避免重复自动下载。", "ok")

    def _on_dl_err(self, msg: str):
        self._last_download_ok = False
        self._last_batch_note = "失败"
        self._set_status("失败 ✗", "#ef4444")
        self._log(f"✗ {msg}", "err")
        self._emit_nav_progress(False)
        self._report_batch_item_done()

    def _on_dl_finished(self):
        self.btn_dl_sel.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        # 任务结束：侧栏进度线消失；成功路径在此结算批处理删行
        self._emit_nav_progress(False)
        if getattr(self, "_last_download_ok", False):
            self._report_batch_item_done()

    def export_settings(self) -> dict:
        """导出B站页设置，供 user_prefs 落盘。"""
        return {
            "save_path": (self.save_edit.text() or "").strip(),
            "cookie_path": (self.ck_path.text() or "").strip(),
        }

    def apply_settings(self, d: dict):
        """从 user.txt 恢复设置。未知/空字段保持现状。"""
        if not isinstance(d, dict):
            return
        path = (d.get("save_path") or "").strip()
        if path:
            self.save_edit.blockSignals(True)
            try:
                self.save_edit.setText(path.replace("\\", "/"))
            finally:
                self.save_edit.blockSignals(False)
        ck = (d.get("cookie_path") or "").strip()
        if ck and os.path.isfile(ck):
            self.ck_path.blockSignals(True)
            try:
                self.ck_path.setText(ck.replace("\\", "/"))
            finally:
                self.ck_path.blockSignals(False)

    def shutdown(self):
        """主窗口关窗时调用：打断解析/下载线程。"""
        from utils.qthread_util import stop_qthreads
        stop_qthreads(
            (getattr(self, "_parse_worker", None), getattr(self, "_dl_worker", None)),
            cancel_flag=getattr(self, "_cancel_flag", None),
        )
        self._emit_nav_progress(False)
