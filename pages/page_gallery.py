# pages/page_gallery.py
# 图集下载 —— 粘贴漫画/图站网址，解析页面内图片源并一键下载
# 优先支持：e-hentai / exhentai 图库；其它站点走通用 HTML 抽图

import json
import os
import re
import time
import html as html_lib
from urllib.parse import urljoin, urlparse

from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize, QRectF, QPointF
from PyQt5.QtGui import QTextCursor, QIcon, QPixmap, QPainter, QColor, QPen, QBrush
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QTextEdit, QFileDialog, QApplication,
    QCheckBox, QComboBox, QFrame, QScrollArea, QSizePolicy,
    QTabWidget, QTabBar, QDialog, QMessageBox,
)

from pages.page_douyin import DouyinProgressLine

from styles.style_all import (
    theme,
    fmt,
    tk,
    TAB_QSS,
    VIDEO_TAB_QSS,
    apply_transparent_surface,
    install_card_title,
    message_box_warn,
    restyle_card_title,
    restyle_card_frame,
    make_card,
    apply_folder_path_edit,
    restyle_folder_path_edit,
    apply_mini_button,
    apply_simple_record,
    CARD_TOP_GAP,
    CARD_LEFT_GAP,
    CARD_RIGHT_GAP,
    CARD_BOTTOM_GAP,
    MEDIUM_BUTTON_H,
)
from utils.logger import get_logger
from utils.flow_layout import FlowLayout
from utils.cursor_toast import (
    show_cursor_toast, in_batch_download, show_batch_progress_toast,
    stop_batch_progress_toast, set_batch_progress_note,
)
from utils.gallery_records import (
    has_record, add_record, scan_dir_records, load_records,
    parse_record_text, save_records,
    is_record_sep, split_suspect_record_lines, format_records_with_suspects,
)

try:
    import requests as req_lib
    HAS_REQUESTS = True
except ImportError:
    req_lib = None
    HAS_REQUESTS = False

log = get_logger(__name__)


class _ElideLabel(QLabel):
    """状态文字过长时省略，不把所在列的最小宽度顶爆。"""

    def __init__(self, text: str = "", parent=None):
        super().__init__("", parent)
        self._full = text or ""
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._apply_elide()

    def setText(self, text):
        self.setFullText(text)

    def setFullText(self, text: str):
        self._full = text or ""
        self._apply_elide()

    def fullText(self) -> str:
        return self._full

    def _apply_elide(self):
        fm = self.fontMetrics()
        avail = max(0, self.width() - 2)
        super().setText(fm.elidedText(self._full, Qt.ElideRight, avail))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_elide()


class _RatioCol(QWidget):
    """双列 stretch 比例始终生效：忽略子控件横向 minimumSizeHint。

    普通 QHBoxLayout 的 stretch 只分配「满足 min 之后」的剩余空间；
    左列 Cookie 标题 / 选项行 / 长 placeholder 的 min 很大时，窄窗会变成 8:2。
    这里强制横向 min=0，由 70:30 stretch 决定列宽，列内控件再自行收缩/省略。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def minimumSizeHint(self):
        sh = super().minimumSizeHint()
        return QSize(0, sh.height())


# 下载间隔：界面用「秒」思考，内部仍存 ms（prefs / 下载线程）
_DELAY_MS_MIN = 0
_DELAY_MS_MAX = 10000  # 最长 10 秒，够慢网/严限额
_DELAY_PRESETS = (
    # (显示, ms) — 文案宜短，避免可编辑下拉框把「1.2」左侧裁掉
    ("无间隔", 0),
    ("0.5 秒", 500),
    ("1.0 秒", 1000),
    ("1.2 秒 · 荐", 1200),
    ("1.5 秒", 1500),
    ("2.0 秒", 2000),
    ("3.0 秒", 3000),
    ("5.0 秒", 5000),
)


def _clamp_delay_ms(ms: int) -> int:
    try:
        v = int(ms)
    except (TypeError, ValueError):
        v = 1200
    return max(_DELAY_MS_MIN, min(_DELAY_MS_MAX, v))


def _format_delay_ms(ms: int) -> str:
    """把毫秒格式化成下拉/编辑框文案。"""
    ms = _clamp_delay_ms(ms)
    if ms <= 0:
        return "无间隔"
    for label, preset in _DELAY_PRESETS:
        if preset == ms and preset > 0:
            return label
    # 整秒 / 一位小数
    sec = ms / 1000.0
    if abs(sec - round(sec)) < 1e-6:
        return f"{int(round(sec))}.0 秒"
    return f"{sec:.1f} 秒"


def _parse_delay_to_ms(text: str) -> int:
    """解析用户输入：1.5 / 1.5秒 / 1500 / 1500ms / 无间隔 → 毫秒。

    规则：带 ms/毫秒 → 毫秒；带 秒/s → 秒；纯数字 ≤10 当秒，>10 当毫秒。
    """
    t = (text or "").strip()
    if not t:
        return 1200
    # 去掉推荐标记、空白
    t = re.sub(r"[（(]推荐[）)]", "", t).strip()
    t_compact = re.sub(r"\s+", "", t).lower()
    if t_compact in ("无", "无间隔", "0", "0秒", "0s", "0.0秒", "0.0s"):
        return 0

    has_ms = t_compact.endswith("ms") or t_compact.endswith("毫秒")
    has_sec = (not has_ms) and (
        t_compact.endswith("秒")
        or t_compact.endswith("s")
        or "秒" in t_compact
    )
    # 抽出第一个数字（支持 1.5）
    m = re.search(r"(\d+(?:\.\d+)?)", t_compact)
    if not m:
        return 1200
    try:
        num = float(m.group(1))
    except ValueError:
        return 1200

    if has_ms:
        return _clamp_delay_ms(int(round(num)))
    if has_sec:
        return _clamp_delay_ms(int(round(num * 1000)))
    # 裸数字：小值当秒（1.5 → 1500），大值当毫秒（1500 → 1500）
    if num <= 10:
        return _clamp_delay_ms(int(round(num * 1000)))
    return _clamp_delay_ms(int(round(num)))


# ── Cookie 自动扫描（对齐 YouTube 页逻辑）────────────────────────────────────

# 扩展导出名优先；用户点名 e-hentai.org_cookies.txt
_EH_COOKIE_PREFERRED_NAMES = (
    "e-hentai.org_cookies.txt",
    "www.e-hentai.org_cookies.txt",
    "exhentai.org_cookies.txt",
    "www.exhentai.org_cookies.txt",
    "e-hentai_cookies.txt",
    "exhentai_cookies.txt",
    "eh_cookies.txt",
    "cookies.txt",
)


def _default_download_dirs() -> list:
    """常见下载目录：用户 Downloads / 下载。"""
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


def _file_has_eh_cookies(path: str) -> bool:
    """粗判 Netscape cookie 是否含 e-hentai / exhentai 域。"""
    try:
        if not path or not os.path.isfile(path):
            return False
        if os.path.getsize(path) > 2 * 1024 * 1024:
            return False
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(65536).lower()
        return (
            "e-hentai.org" in head
            or "exhentai.org" in head
            or ".e-hentai." in head
            or ".exhentai." in head
        )
    except Exception:
        return False


def _score_eh_cookie_file(path: str) -> int:
    """评分：优先完整登录态 + 字段多；无效返回 -1。"""
    if not _file_has_eh_cookies(path):
        return -1
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except Exception:
        return -1
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    score = len(lines)
    # e-hentai / exhentai 常见登录相关字段
    strong_keys = (
        "ipb_member_id", "ipb_pass_hash", "ipb_session_id",
        "sk", "igneous", "yay", "s",
    )
    for k in strong_keys:
        if k in text:
            score += 200
    base = os.path.basename(path).lower()
    for i, pref in enumerate(_EH_COOKIE_PREFERRED_NAMES):
        if base == pref.lower():
            score += 500 - i * 40
            break
    if "exhentai" in base:
        score += 120
    elif "e-hentai" in base or "ehentai" in base or base.startswith("eh_"):
        score += 100
    return score


def find_eh_cookie_file(search_dirs) -> str:
    """在目录顶层找最合适的 e-hentai / exhentai Netscape Cookie。

    优先：e-hentai.org_cookies.txt 等常见导出名；
    其次：任意含 e-hentai.org / exhentai.org 的 .txt。
    找不到返回 ""。
    """
    for d in search_dirs or []:
        if not d or not os.path.isdir(d):
            continue
        for name in _EH_COOKIE_PREFERRED_NAMES:
            p = os.path.join(d, name)
            if os.path.isfile(p) and _file_has_eh_cookies(p):
                return os.path.normpath(p)

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
            sc = _score_eh_cookie_file(path)
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


# ── Pixiv 辅助 ────────────────────────────────────────────────────────────────

_PIXIV_ARTWORK_RE = re.compile(
    r"https?://(?:www\.)?pixiv\.net/(?:en/)?artworks/(\d+)",
    re.I,
)

_PIXIV_PRELOAD_RE = re.compile(
    r'<meta\s+name=["\']preload-data["\'][^>]*content=[\'"]([^\'"]+)[\'"]',
    re.I,
)

_PIXIV_COOKIE_PREFERRED_NAMES = (
    "www.pixiv.net_cookies.txt",
    "www.pixiv.net_cookie.txt",
    "pixiv.net_cookies.txt",
    "pixiv.net_cookie.txt",
    "pixiv_cookies.txt",
    "pixiv_cookie.txt",
    "cookies.txt",
)


def _file_has_pixiv_cookies(path: str) -> bool:
    try:
        if not path or not os.path.isfile(path):
            return False
        if os.path.getsize(path) > 2 * 1024 * 1024:
            return False
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(65536).lower()
        return "pixiv.net" in head or ".pixiv." in head or "pixiv" in head
    except Exception:
        return False


def find_pixiv_cookie_file(search_dirs) -> str:
    for d in search_dirs or []:
        if not d or not os.path.isdir(d):
            continue
        for name in _PIXIV_COOKIE_PREFERRED_NAMES:
            p = os.path.join(d, name)
            if os.path.isfile(p) and _file_has_pixiv_cookies(p):
                return os.path.normpath(p)
    for d in search_dirs or []:
        if not d or not os.path.isdir(d):
            continue
        try:
            names = os.listdir(d)
        except Exception:
            continue
        candidates = []
        for name in names:
            if not name.lower().endswith(".txt"):
                continue
            low = name.lower()
            if low in ("readme.txt", "license.txt", "changelog.txt"):
                continue
            path = os.path.join(d, name)
            if not os.path.isfile(path):
                continue
            try:
                if os.path.getsize(path) > 2 * 1024 * 1024:
                    continue
            except Exception:
                continue
            if _file_has_pixiv_cookies(path):
                try:
                    mtime = os.path.getmtime(path)
                except Exception:
                    mtime = 0
                candidates.append((mtime, path))
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            return os.path.normpath(candidates[0][1])
    return ""


# ── Hitomi.la 辅助（无需 Cookie）────────────────────────────────────────────

# 路径形态：…/type/slug-标题-4091293.html  或  …/galleries/4091293.html
# 注意：slug 里常含 chapter-4 这类短数字，不能用「第一个 \d+」去抓 ID。
_HITOMI_ID_RE = re.compile(
    r"hitomi\.la/(?:galleries|reader|doujinshi|manga|artistcg|gamecg|imageset|anime)/"
    r"[^\s\"'<>]*?-(\d{4,10})(?:\.html)?(?:[#?]|$)",
    re.I,
)
_HITOMI_GALLERIES_ID_RE = re.compile(
    r"hitomi\.la/galleries/(\d{4,10})(?:\.html)?(?:[#?]|$)",
    re.I,
)
_HITOMI_ID_BARE_RE = re.compile(r"(?:^|[^\d])(\d{5,10})(?:\.html)?(?:$|[^\d#?])")
_HITOMI_LTN_HOSTS = (
    "ltn.gold-usergeneratedcontent.net",
    "ltn.hitomi.la",
)
_HITOMI_CDN_DOMAIN = "gold-usergeneratedcontent.net"
# gg.js 的 b 前缀与 m() 分支会随时间轮换；缓存过久会拼出错子域（404 / 错图）
# m(g) = (g 在 case 列表 ? case_m : default_m)；历史上 default/case 会整体翻转
_HITOMI_GG_CACHE = {
    "ts": 0.0,
    "cases": None,
    "default_m": 0,
    "case_m": 1,
    "b": "",
}
_HITOMI_GG_TTL = 60.0  # 秒


def extract_hitomi_id(text: str) -> int:
    """从文本中提取 hitomi.la 图库 ID。

    优先：…/slug-4091293.html 末尾连字符后的 ID（避免 chapter-4 误匹配）。
    其次：…/galleries/4091293.html；再退回纯数字 / 文本中最长的候选 ID。
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("空文本")
    # 1) /galleries/{id}.html
    m = _HITOMI_GALLERIES_ID_RE.search(text)
    if m:
        return int(m.group(1))
    # 2) /type/…-{id}.html  （标准阅读/详情页）
    m = _HITOMI_ID_RE.search(text)
    if m:
        return int(m.group(1))
    # 3) 纯数字
    if re.fullmatch(r"\d{4,10}", text):
        return int(text)
    # 4) 任意 hitomi URL：取 .html 前最后一个 4～10 位数字
    m2 = re.search(
        r"hitomi\.la/[^\s\"'<>]*?-(\d{4,10})(?:\.html)?(?:[#?\s]|$)",
        text,
        re.I,
    )
    if m2:
        return int(m2.group(1))
    # 5) 文本中所有候选，取最长（通常是真正的 gallery id）
    cands = re.findall(r"(\d{5,10})", text)
    if cands:
        return int(max(cands, key=len))
    raise ValueError("未找到 hitomi.la 图库 ID（请粘贴图库/阅读页链接）")


def _hitomi_fetch_text(session, path: str, log=None) -> str:
    """从 ltn 主机拉文本；多 host 轮换 + 重试。"""
    import time as _time
    last_err = None
    for attempt in range(3):
        for host in _HITOMI_LTN_HOSTS:
            url = f"https://{host}{path}"
            try:
                r = session.get(url, timeout=30, headers={"Referer": "https://hitomi.la/"})
                if r.status_code == 404:
                    last_err = RuntimeError(f"404 {url}")
                    continue
                r.raise_for_status()
                return r.text
            except Exception as e:
                last_err = e
                if log:
                    log(f"  ltn 主机失败 {host}：{e}", "warn")
        if attempt < 2:
            delay = (attempt + 1) * 2.0
            if log:
                log(f"  等待 {delay:.0f}s 后重试…", "info")
            _time.sleep(delay)
    raise RuntimeError(f"无法访问 Hitomi 元数据：{last_err}")


def _hitomi_load_gg(session, log=None, force: bool = False) -> tuple:
    """解析 gg.js → (cases:set, default_m:int, case_m:int, b:str)。

    对齐官网 common.js / download.js（gg.m 会周期性翻转 default/case）：
      gg.m(g) = (g 在 case 列表 ? case_m : default_m)
      例 A：var o=1; case: o=0  → default_m=1, case_m=0
      例 B：var o=0; case: o=1  → default_m=0, case_m=1  （当前）
      gg.s(h) = parseInt(末位+倒数2-3位, 16)
      full_path = gg.b + gg.s(h) + '/' + h
      webp 子域 = 'w' + (1 + gg.m(g))
    """
    now = time.time()
    cache = _HITOMI_GG_CACHE
    if (
        not force
        and cache.get("cases") is not None
        and (now - float(cache.get("ts") or 0)) < _HITOMI_GG_TTL
    ):
        return (
            cache["cases"],
            int(cache.get("default_m") or 0),
            int(cache.get("case_m") if cache.get("case_m") is not None else 1),
            cache["b"],
        )
    if log:
        log("拉取 gg.js（图床路由，与官网 Download 一致）…", "info")
    text = _hitomi_fetch_text(session, "/gg.js", log=log)
    cases = set(int(x) for x in re.findall(r"case\s+(\d+)\s*:", text))
    if not cases:
        raise RuntimeError("gg.js 解析失败：未找到 case 列表（算法可能已更新）")
    # m 函数：var o = D; switch … case: o = C; break; return o;
    mo = re.search(r"var\s+o\s*=\s*(\d+)\s*;", text)
    default_m = int(mo.group(1)) if mo else 0
    case_assigns = re.findall(r"o\s*=\s*(\d+)\s*;\s*break", text)
    if case_assigns:
        case_m = int(case_assigns[-1])
    else:
        # 兜底：与 default 相反
        case_m = 0 if default_m else 1
    mb = re.search(r"b:\s*'([^']+)'", text)
    if not mb:
        mb = re.search(r'b:\s*"([^"]+)"', text)
    b = mb.group(1) if mb else ""
    if not b:
        raise RuntimeError("gg.js 解析失败：未找到 b 路径前缀")
    if not b.endswith("/"):
        b = b + "/"
    cache["ts"] = now
    cache["cases"] = cases
    cache["default_m"] = default_m
    cache["case_m"] = case_m
    cache["b"] = b
    if log:
        log(
            f"gg.js 就绪 · cases={len(cases)} · m=({default_m}/{case_m}) · b={b}",
            "ok",
        )
    return cases, default_m, case_m, b


def _hitomi_gg_s(hash_: str) -> str:
    """官网 gg.s(h)：hash 末 3 位重排后的十进制字符串。"""
    h = (hash_ or "").strip()
    if len(h) < 3:
        raise ValueError(f"无效 hash：{hash_!r}")
    m = re.search(r"(..)(.)$", h)
    if not m:
        raise ValueError(f"无效 hash：{hash_!r}")
    return str(int(m.group(2) + m.group(1), 16))


def _hitomi_gg_m(code: int, cases: set, default_m: int = 0, case_m: int = 1) -> int:
    """官网 gg.m(g)：命中 case → case_m，否则 default_m。"""
    return int(case_m) if int(code) in cases else int(default_m)


def _hitomi_webp_url(
    hash_: str, cases: set, default_m: int, case_m: int, b: str
) -> str:
    """按官网 download.js：url_from_url_from_hash(..., 'webp') 拼直链。

    必须带 Referer: https://hitomi.la/ 才能 200，否则 CDN 常回 404 HTML。
    """
    h = (hash_ or "").strip()
    code_s = _hitomi_gg_s(h)
    code = int(code_s)
    # subdomain_from_url(dir=webp)：retval='w' + (1+gg.m(g))
    sub = "w" + str(1 + _hitomi_gg_m(code, cases, default_m, case_m))
    # full_path_from_hash：gg.b + gg.s(h) + '/' + h
    path = f"{b}{code_s}/{h}.webp"
    return f"https://{sub}.{_HITOMI_CDN_DOMAIN}/{path}"


def _hitomi_zip_entry_name(orig_name: str, index: int) -> str:
    """官网：image.name 扩展名改成 webp（如 01.png → 01.webp）。"""
    raw = (orig_name or "").strip() or f"{index:04d}.png"
    base = os.path.basename(raw.replace("\\", "/"))
    if "." in base:
        stem = base.rsplit(".", 1)[0]
    else:
        stem = base
    stem = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", stem).strip(" .") or f"{index:04d}"
    return f"{stem}.webp"


def debug_hitomi_gallery(url_or_id, *, sample: int = 8, log=None) -> dict:
    """Hitomi 调试：官网 Download 机制说明 + gg/webp 直链抽样。

    重要结论（与 tools/hitomi_zip_debug.py 一致）：
      官网「Download」= 浏览器内逐张拉 webp + JSZip 打包，**没有**服务端整包 .zip。
      主程序 HitomiZipDownloadWorker 即复刻该流程。

    完整取证/实测打包请运行：
      python tools/hitomi_zip_debug.py <链接或ID>
    """
    from datetime import datetime

    lines = []

    def _lg(msg, level="info"):
        lines.append(f"[{level}] {msg}")
        if log:
            try:
                log(msg, level)
            except Exception:
                pass

    report = {
        "ok": False,
        "gallery_id": None,
        "title": "",
        "zip_name": "",
        "gg": {},
        "sample": [],
        "ok_n": 0,
        "fail_n": 0,
        "report_path": "",
        "error": "",
        "mechanism": "client_jszip",  # 非 server_zip
        "server_zip": False,
    }
    try:
        gid = extract_hitomi_id(str(url_or_id))
    except Exception as e:
        report["error"] = str(e)
        _lg(str(e), "err")
        return report
    report["gallery_id"] = gid
    _lg(f"图库 ID：{gid}", "info")
    _lg(
        "官网 Download = 客户端 JSZip（download.js 逐张 webp），无服务端整包 zip",
        "info",
    )
    try:
        session = _make_session("")
        # 快速确认 download.js 仍是客户端打包
        try:
            dj = session.get(
                f"https://{_HITOMI_LTN_HOSTS[0]}/download.js",
                timeout=20,
                headers={"Referer": "https://hitomi.la/"},
            )
            if dj.status_code == 200 and "JSZip" in (dj.text or ""):
                report["mechanism"] = "client_jszip"
                report["server_zip"] = False
                _lg("download.js 含 JSZip · 确认客户端打包", "ok")
            else:
                _lg(f"download.js 异常 HTTP {dj.status_code}，仍按客户端流程测直链", "warn")
        except Exception as e:
            _lg(f"拉 download.js 跳过：{e}", "warn")

        cases, default_m, case_m, b = _hitomi_load_gg(
            session, log=_lg, force=True
        )
        report["gg"] = {
            "cases": len(cases),
            "default_m": default_m,
            "case_m": case_m,
            "b": b,
        }
        info = parse_hitomi_gallery(
            session, gid, log=_lg, cancel_flag=None
        )
        title = info.get("title") or f"hitomi_{gid}"
        zip_base = _safe_folder_name(title, "hitomi")
        report["title"] = title
        report["zip_name"] = f"{zip_base}.zip"
        _lg(f"标题：{title}", "ok")
        _lg(f"官网风格 zip 名：{report['zip_name']}", "ok")
        images = info.get("images") or []
        n = min(max(1, int(sample)), len(images))
        ok_n = fail_n = 0
        for it in images[:n]:
            url = it.get("url") or ""
            # 强制用最新 gg 重算
            h = (it.get("hash") or "").strip()
            if h:
                url = _hitomi_webp_url(h, cases, default_m, case_m, b)
            row = {
                "name": it.get("zip_name") or it.get("name"),
                "url": url,
                "status": 0,
                "bytes": 0,
                "content_type": "",
                "magic": "",
                "ok": False,
            }
            try:
                r = session.get(
                    url,
                    headers={
                        "Referer": "https://hitomi.la/",
                        "Accept": "image/webp,image/*,*/*;q=0.8",
                    },
                    timeout=(15, 60),
                )
                row["status"] = int(r.status_code)
                row["bytes"] = len(r.content or b"")
                row["content_type"] = (r.headers.get("Content-Type") or "")[:60]
                magic = (r.content or b"")[:12]
                row["magic"] = magic[:4].hex()
                is_webp = (
                    len(magic) >= 12
                    and magic[:4] == b"RIFF"
                    and magic[8:12] == b"WEBP"
                )
                row["ok"] = r.status_code == 200 and is_webp and row["bytes"] > 256
                if row["ok"]:
                    ok_n += 1
                    _lg(
                        f"  ✓ {row['name']} HTTP {row['status']} {row['bytes']}B webp",
                        "ok",
                    )
                else:
                    fail_n += 1
                    _lg(
                        f"  ✗ {row['name']} HTTP {row['status']} "
                        f"ct={row['content_type']} magic={row['magic']}",
                        "warn",
                    )
            except Exception as e:
                fail_n += 1
                row["error"] = str(e)
                _lg(f"  ✗ {row['name']}：{e}", "err")
            report["sample"].append(row)
        report["ok_n"] = ok_n
        report["fail_n"] = fail_n
        report["ok"] = fail_n == 0 and ok_n > 0
        _lg(
            f"抽样 {n} 张：成功 {ok_n} · 失败 {fail_n}"
            + (" · 路由正常" if report["ok"] else " · 请检查 gg.m / b"),
            "ok" if report["ok"] else "warn",
        )
        _lg(
            "完整取证/整包实测：python tools/hitomi_zip_debug.py <链接>",
            "info",
        )
    except Exception as e:
        report["error"] = str(e)
        _lg(f"调试失败：{e}", "err")

    # 落盘
    try:
        from utils.app_paths import records_file
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = records_file(f"hitomi_debug_{stamp}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("Hitomi 直链调试报告\n")
            f.write(f"time: {stamp}\n")
            f.write(f"input: {url_or_id}\n")
            f.write(f"gallery_id: {report.get('gallery_id')}\n")
            f.write(f"title: {report.get('title')}\n")
            f.write(f"zip_name: {report.get('zip_name')}\n")
            f.write(f"mechanism: {report.get('mechanism')} (server_zip={report.get('server_zip')})\n")
            f.write(f"gg: {report.get('gg')}\n")
            f.write(f"ok_n/fail_n: {report.get('ok_n')}/{report.get('fail_n')}\n")
            f.write(f"error: {report.get('error')}\n")
            f.write(
                "note: 官网 Download 为客户端 JSZip，无服务端整包；"
                "详见 tools/hitomi_zip_debug.py\n\n"
            )
            for row in report.get("sample") or []:
                f.write(
                    f"{'OK' if row.get('ok') else 'FAIL'} "
                    f"{row.get('name')} HTTP {row.get('status')} "
                    f"{row.get('bytes')}B {row.get('content_type')}\n"
                    f"  {row.get('url')}\n"
                )
            f.write("\n── log ──\n")
            f.write("\n".join(lines))
            f.write("\n")
        report["report_path"] = path
        _lg(f"报告已写入：{path}", "ok")
    except Exception as e:
        _lg(f"写报告失败：{e}", "warn")
    return report


def parse_hitomi_gallery(session, gallery_id, log=None, cancel_flag=None, title_callback=None) -> dict:
    """解析 hitomi.la 图库（无需 Cookie）。

    与官网「Download」按钮同源：只取 webp 直链，下载阶段再打成 zip。
    """
    gid = int(gallery_id)
    if log:
        log(f"正在获取图库 {gid} …", "info")

    def _cancelled():
        return bool(cancel_flag and cancel_flag[0])

    if _cancelled():
        raise RuntimeError("用户取消")

    raw = _hitomi_fetch_text(session, f"/galleries/{gid}.js", log=log)
    if _cancelled():
        raise RuntimeError("用户取消")
    # var galleryinfo = {...};
    body = raw.strip()
    if "galleryinfo" in body[:80]:
        body = body.split("=", 1)[1].strip().rstrip(";").strip()
    try:
        data = json.loads(body)
    except Exception as e:
        raise RuntimeError(f"图库元数据 JSON 解析失败：{e}") from e

    # 官网 gallery.js：download_gallery(japanese_title || title)
    title = (
        data.get("japanese_title") or data.get("title") or f"hitomi_{gid}"
    ).strip()
    if callable(title_callback):
        try:
            title_callback(title)
        except Exception:
            pass
    files = data.get("files") or []
    if not files:
        raise RuntimeError("图库无图片列表")

    # 使用缓存的 gg.js（60s TTL，批处理时避免逐条刷取）
    cases, default_m, case_m, b = _hitomi_load_gg(session, log=log, force=False)
    if _cancelled():
        raise RuntimeError("用户取消")

    images = []
    referer = "https://hitomi.la/"
    source_url = f"https://hitomi.la/galleries/{gid}.html"
    for i, fi in enumerate(files):
        if _cancelled():
            raise RuntimeError("用户取消")
        if not isinstance(fi, dict):
            continue
        h = (fi.get("hash") or "").strip()
        if not h:
            continue
        # 官网 Download 固定 dir='webp'
        url = _hitomi_webp_url(h, cases, default_m, case_m, b)
        zip_name = _hitomi_zip_entry_name(fi.get("name") or "", i + 1)
        name = f"{i + 1:04d}"  # UI 卡序号
        images.append({
            "url": url,
            "url_alts": [],
            "referer": referer,
            "index": i + 1,
            "name": name,
            "zip_name": zip_name,
            "ext": ".webp",
            "hash": h,
        })

    if not images:
        raise RuntimeError("未能生成任何图片地址")

    if log:
        log(
            f"解析完成：{title[:60]} · {len(images)} 张 · 将打成单个 .zip"
            f"（官网 Download 同款：客户端打包，无服务端整包）",
            "ok",
        )

    return {
        "title": title,
        "site": "hitomi.la",
        "source_url": source_url,
        "gallery_id": gid,
        "images": images,
        "download_as_zip": True,
    }


def parse_pixiv_page(session, url: str, log=None, cancel_flag=None) -> dict:
    """解析 pixiv.net/artworks/{id}，提取图片 URL 列表。"""
    m = _PIXIV_ARTWORK_RE.search(url)
    if not m:
        raise RuntimeError("不是有效的 pixiv 作品链接")
    illust_id = m.group(1)

    if log:
        log(f"正在获取作品 {illust_id} …", "info")

    # pixiv 严格检查 Referer + 浏览器头
    headers = {
        "Referer": "https://www.pixiv.net/",
        "Origin": "https://www.pixiv.net",
        "Accept": "application/json, text/plain, */*",
    }

    data = None
    # 优先使用 pixiv 内部 AJAX API（更可靠）
    api_url = f"https://www.pixiv.net/ajax/illust/{illust_id}"
    try:
        r = session.get(api_url, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception:
        if log:
            log("AJAX 接口失败，尝试解析页面…", "warn")

    # 回退：从 HTML meta 标签提取
    if not data or not isinstance(data, dict):
        try:
            r = session.get(url, headers={"Referer": "https://www.pixiv.net/"}, timeout=30)
            r.raise_for_status()
            body = r.text
            preload_m = _PIXIV_PRELOAD_RE.search(body)
            if not preload_m:
                raise RuntimeError(
                    "无法从页面提取作品数据（可能需要登录 Cookie）。\n"
                    "请先在 pixiv.net 登录后导出 cookies.txt 并加载到本页"
                )
            raw = html_lib.unescape(preload_m.group(1))
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise RuntimeError(
                "无法从页面提取作品数据（可能需要登录 Cookie）。\n"
                "请先在 pixiv.net 登录后导出 cookies.txt 并加载到本页"
            )

    if not data or not isinstance(data, dict):
        raise RuntimeError(
            "获取作品数据失败。请确认：\n"
            "  1. 已在浏览器登录 pixiv.net\n"
            "  2. 已用「Get cookies.txt LOCALLY」扩展导出当前域 cookies\n"
            "  3. 文件保存为 .txt 格式并加载到本页"
        )

    # 从 AJAX 响应中提取 illust 数据
    illust_data = None
    if "body" in data:
        illust_data = data["body"]
    elif "illust" in data:
        illust_data = (data.get("illust") or {}).get(illust_id)

    if not illust_data or not isinstance(illust_data, dict):
        raise RuntimeError(f"JSON 中未找到作品 {illust_id}，可能需要登录")

    title = illust_data.get("title") or illust_data.get("illustTitle") or f"pixiv_{illust_id}"
    page_count = int(illust_data.get("pageCount") or 1)
    urls = illust_data.get("urls") or {}
    original = urls.get("original") or ""

    if not original:
        raise RuntimeError("无法获取原始图片地址（可能需要登录 Cookie）")

    # 注意：i.pximg.net 的防盗链校验只认「裸域名」Referer（https://www.pixiv.net/），
    # 用带 /artworks/xxxx 路径的完整页面地址当 Referer 会被判定不匹配而返回 403。
    # （pixiv.net 页面本身/AJAX 接口不做这个限制，所以解析阶段用完整 url 没问题，
    # 只有真正请求图片 CDN 时才需要这个裸域名。）
    img_referer = "https://www.pixiv.net/"

    images = []
    if page_count <= 1:
        images.append({
            "url": original,
            "referer": img_referer,
            "index": 0,
            "name": "0001",
        })
    else:
        # 多页作品：original 是 _p0，后续页替换 _p\d+ → _p{pi}
        base, ext = os.path.splitext(original)
        # 去掉末尾已有页码（如 _p0），稍后统一加 _p{pi}
        base_no_p = re.sub(r'_p\d+$', '', base)
        for pi in range(page_count):
            pn = f"{base_no_p}_p{pi}{ext}"
            images.append({
                "url": pn,
                "referer": img_referer,
                "index": pi,
                "name": f"{pi:04d}",
            })

    if log:
        log(f"解析完成：{title} · {len(images)} 张", "ok")

    return {
        "site": "pixiv.net",
        "title": _sanitize_title(title),
        "source_url": url,
        "images": images,
        "filecount": len(images),
    }


def _sanitize_title(title: str) -> str:
    t = (title or "").strip()
    t = re.sub(r'[\\/:*?"<>|]', "_", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:80] if t else "pixiv"


# ── 常量 / 正则 ──────────────────────────────────────────────────────────────

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_EH_GALLERY_RE = re.compile(
    r"https?://(?:e-hentai\.org|exhentai\.org)/g/(\d+)/([0-9a-fA-F]+)/?",
    re.I,
)
_EH_PAGE_RE = re.compile(
    r"https?://(?:e-hentai\.org|exhentai\.org)/s/([0-9a-fA-F]+)/(\d+)-(\d+)",
    re.I,
)
# 图库缩略图 → 阅读页（绝对 URL 或站内相对 /s/…）
_EH_SHOW_HREF_RE = re.compile(
    r'href=["\']((?:https?://(?:e-hentai\.org|exhentai\.org))?/s/[0-9a-fA-F]+/\d+-\d+)["\']',
    re.I,
)
_EH_IMG_ID_RE = re.compile(
    r'<img[^>]+id=["\']img["\'][^>]+src=["\']([^"\']+)["\']',
    re.I,
)
_EH_IMG_ID_RE2 = re.compile(
    r'<img[^>]+src=["\']([^"\']+)["\'][^>]+id=["\']img["\']',
    re.I,
)
_EH_TITLE_RE = re.compile(
    r'<h1[^>]*id=["\']gn["\'][^>]*>(.*?)</h1>',
    re.I | re.S,
)
_EH_FILECOUNT_RE = re.compile(
    r"(?:Length|页数|ファイル数)[:\s]*</td>\s*<td[^>]*>\s*(\d+)\s*(?:pages?|页|ページ)",
    re.I,
)
_EH_FILECOUNT_RE2 = re.compile(r"(\d+)\s+pages?", re.I)
_EH_NEXT_RE = re.compile(
    r'<a\s+id=["\']next["\'][^>]+href=["\']([^"\']+)["\']',
    re.I,
)
_EH_SKIP_HATH_RE = re.compile(
    r"You have exceeded your image viewing limits|This IP address has been|Your IP address has been temporarily banned",
    re.I,
)

_GENERIC_IMG_ATTRS = (
    r'src=["\']([^"\']+)["\']',
    r'data-src=["\']([^"\']+)["\']',
    r'data-original=["\']([^"\']+)["\']',
    r'data-lazy-src=["\']([^"\']+)["\']',
    r'data-url=["\']([^"\']+)["\']',
    r'data-full=["\']([^"\']+)["\']',
    r'content=["\'](https?://[^"\']+\.(?:jpe?g|png|webp|gif|bmp|avif)[^"\']*)["\']',
)
_SRCSET_RE = re.compile(r'srcset=["\']([^"\']+)["\']', re.I)
_IMG_EXT_RE = re.compile(
    r"\.(jpe?g|png|webp|gif|bmp|avif|jfif)(?:\?|#|$)",
    re.I,
)
_SKIP_URL_HINTS = (
    "logo", "avatar", "icon", "sprite", "emoji", "badge",
    "button", "pixel", "tracker", "ads.", "doubleclick",
    "favicon", "1x1", "blank.", "spacer",
)


def _append_project_name(text: str, project: str, max_len: int = 56) -> str:
    """状态行后缀：追加项目名（下载目录名），过长截断并避免重复拼接。"""
    name = (project or "").strip()
    if not name:
        return text or ""
    if len(name) > max_len:
        name = name[: max_len - 1].rstrip(" .") + "…"
    base = (text or "").rstrip()
    if not base:
        return name
    # 已含同名则不重复（解析成功行、已打包 zip 行等）
    if name in base or base.endswith(name):
        return base
    return f"{base} · {name}"


def _status_needs_project(text: str) -> bool:
    """下载过程/完成后的状态行需要带项目名。"""
    s = (text or "").strip()
    if not s:
        return False
    keys = (
        "已下 ", "已下", "完成：", "准备下载", "下载中",
        "% ·", "%·", "已打包", "失败", "仍缺",
    )
    return any(k in s for k in keys)


def _safe_folder_name(name: str, fallback: str = "gallery", max_len: int = 80) -> str:
    """生成可在 Windows 上安全落盘的目录名。

    注意：Windows 创建目录时会剥掉路径段末尾的空格/点，但 open() 仍用原字符串，
    若截断后留下尾随空格，会出现「makedirs 成功、写文件报 ENOENT」的坑。
    因此必须：html 反转义 → 替换非法字符 → 截断 → **再次** strip 尾部空格/点。
    """
    name = html_lib.unescape(name or "").strip()
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name)
    # 压缩连续空白 / 下划线，避免难读路径
    name = re.sub(r"[ \t]+", " ", name)
    name = re.sub(r"_+", "_", name)
    name = name.strip(" .") or fallback
    if len(name) > max_len:
        name = name[:max_len].rstrip(" .") or fallback
    # Windows 保留设备名（CON/PRN/…）不能作目录名
    if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", name):
        name = f"_{name}"
    return name


def _guess_ext(url: str, content_type: str = "") -> str:
    path = urlparse(url).path
    m = re.search(r"\.(jpe?g|png|webp|gif|bmp|avif|jfif)$", path, re.I)
    if m:
        ext = m.group(1).lower()
        return "jpg" if ext in ("jpeg", "jfif") else ext
    ct = (content_type or "").lower()
    if "jpeg" in ct or "jpg" in ct:
        return "jpg"
    if "png" in ct:
        return "png"
    if "webp" in ct:
        return "webp"
    if "gif" in ct:
        return "gif"
    if "avif" in ct:
        return "avif"
    return "jpg"


def _guess_ext_from_file(path: str) -> str:
    """从文件头魔数推测扩展名。"""
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except Exception:
        return ""
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"GIF8"):
        return "gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "webp"
    if head[4:12] == b"ftypavif":
        return "avif"
    return ""


def _looks_like_image_url(url: str) -> bool:
    if not url or url.startswith("data:"):
        return False
    low = url.lower()
    if any(h in low for h in _SKIP_URL_HINTS):
        return False
    if _IMG_EXT_RE.search(low):
        return True
    # e-hentai / 常见 CDN 无扩展名直链
    if any(k in low for k in (
        "hath.network", "ehgt.org", "exhentai.org", "e-hentai.org",
        "i.nhentai", "cdn.", "/full/", "/original/", "img.",
    )):
        return True
    return False


def extract_url_from_text(text: str) -> str:
    text = (text or "").strip()
    if not text:
        raise ValueError("空文本")
    m = re.search(r"(https?://[^\s<>\"']+)", text, re.I)
    if m:
        return m.group(1).rstrip(").,];'\"")
    if text.startswith("www."):
        return "https://" + text.split()[0].rstrip(").,];'\"")
    raise ValueError("未找到有效的 http(s) 链接")


def _batch_line_matches_url(line: str, url: str) -> bool:
    """记录文件行是否对应当前 URL（整行/包含/尾斜杠/分享文案）。"""
    a = (line or "").strip()
    b = (url or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    a0, b0 = a.rstrip("/"), b.rstrip("/")
    if a0 and b0 and a0 == b0:
        return True
    if b in a or b0 in a or a in b or a0 in b0:
        return True
    try:
        m = re.search(r"(https?://[^\s<>\"']+)", a, re.I)
        if m:
            u = m.group(1).rstrip(").,];'\"")
            if u == b or u.rstrip("/") == b0:
                return True
    except Exception:
        pass
    return False


def _batch_remove_url_from_file(path: str, url: str) -> bool:
    """从批处理记录文件中删除匹配 url 的第一行。成功返回 True。"""
    path = (path or "").strip()
    url = (url or "").strip()
    if not path or not url or not os.path.isfile(path):
        return False
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            lines = f.readlines()
    except Exception:
        return False
    new_lines = []
    found = False
    for line in lines:
        if not found and _batch_line_matches_url(line, url):
            found = True
            continue
        new_lines.append(line)
    if not found:
        return False
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def _find_unified_batch_engine(widget):
    """向上查找正在跑自动下载的 PageVideo（MainWindow.page_douyin）。"""
    w = widget
    while w is not None:
        if hasattr(w, "notify_batch_item_done") and getattr(w, "_batch_mode", False):
            return w
        pv = getattr(w, "page_douyin", None)
        if (
            pv is not None
            and hasattr(pv, "notify_batch_item_done")
            and getattr(pv, "_batch_mode", False)
        ):
            return pv
        try:
            w = w.parent()
        except Exception:
            break
    return None


def load_netscape_cookies(path: str):
    """简易 Netscape cookie → requests.cookies.RequestsCookieJar 兼容 dict 列表。"""
    if not path or not os.path.isfile(path):
        return []
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                domain, _flag, cpath, _secure, _exp, name, value = parts[:7]
                out.append({
                    "domain": domain.lstrip("."),
                    "path": cpath or "/",
                    "name": name,
                    "value": value,
                })
    except Exception:
        return []
    return out


def _make_session(cookie_path: str = "") -> "req_lib.Session":
    if not HAS_REQUESTS:
        raise RuntimeError("未安装 requests，请 pip install requests")
    s = req_lib.Session()
    s.trust_env = False
    s.headers.update({
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    for c in load_netscape_cookies(cookie_path):
        try:
            s.cookies.set(
                c["name"], c["value"],
                domain=c["domain"], path=c["path"],
            )
        except Exception:
            pass
    return s


# ── 解析引擎 ─────────────────────────────────────────────────────────────────

def eh_api_metadata(session, gid: int, token: str) -> dict:
    """e-hentai 官方 API：标题 / 页数等。失败返回 {}。"""
    try:
        r = session.post(
            "https://api.e-hentai.org/api.php",
            json={"method": "gdata", "gidlist": [[gid, token]], "namespace": 1},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        meta = (data.get("gmetadata") or [None])[0] or {}
        if meta.get("error"):
            return {}
        return meta
    except Exception:
        return {}


def _eh_show_page_num(show_url: str) -> int:
    """阅读页 URL …/s/token/gid-页码 → 1-based 页码；失败返回 0。"""
    m = _EH_PAGE_RE.search(show_url or "")
    if not m:
        return 0
    try:
        return int(m.group(3))
    except (TypeError, ValueError):
        return 0


def _eh_normalize_only_indices(only_indices) -> set:
    """把 442 / '0442' / '0442.jpg' 等统一成 1-based 页码集合。"""
    out = set()
    for x in only_indices or []:
        if x is None:
            continue
        if isinstance(x, int):
            if x > 0:
                out.add(int(x))
            continue
        s = os.path.splitext(str(x).strip())[0]
        m = re.search(r"(\d+)$", s)
        if m:
            try:
                n = int(m.group(1))
            except ValueError:
                continue
            if n > 0:
                out.add(n)
    return out


def _eh_collect_show_links(html: str, host: str = "e-hentai.org"):
    """从图库缩略图 HTML 收集阅读链。

    返回 (ordered_urls, page_num→url)。相对路径 /s/… 会补成 https://{host}/s/…
    """
    ordered = []
    by_page = {}
    seen = set()
    host = (host or "e-hentai.org").strip() or "e-hentai.org"
    for hm in _EH_SHOW_HREF_RE.finditer(html or ""):
        u = (hm.group(1) or "").strip()
        if u.startswith("/s/"):
            u = f"https://{host}{u}"
        if u in seen:
            continue
        seen.add(u)
        ordered.append(u)
        pn = _eh_show_page_num(u)
        if pn > 0 and pn not in by_page:
            by_page[pn] = u
    return ordered, by_page


def parse_eh_gallery(
    session,
    url: str,
    log=None,
    cancel_flag=None,
    title_callback=None,
    only_indices=None,
) -> dict:
    """解析 e-hentai / exhentai 图库，返回 {title, images:[{url,referer,index,name}], site}。

    only_indices:
      若给出（1-based 页码或 '0442' 等），只解析这些页：
        · 缩略图只翻可能含目标的分页
        · 阅读页只打开目标页
      全本解析时保持 only_indices=None（原逻辑）。
    """
    m = _EH_GALLERY_RE.search(url)
    if not m:
        raise ValueError("不是 e-hentai / exhentai 图库链接（/g/数字/token/）")
    gid, token = int(m.group(1)), m.group(2)
    host = "exhentai.org" if "exhentai.org" in url.lower() else "e-hentai.org"
    base = f"https://{host}/g/{gid}/{token}/"
    only = _eh_normalize_only_indices(only_indices)
    selective = bool(only)

    if log:
        if selective:
            prev = "、".join(f"{n:04d}" for n in sorted(only)[:12])
            more = f" 等{len(only)}页" if len(only) > 12 else f" · 共{len(only)}页"
            log(f"识别为 {host} 图库 · gid={gid} · 定点补全 {prev}{more}", "info")
        else:
            log(f"识别为 {host} 图库 · gid={gid}", "info")

    meta = eh_api_metadata(session, gid, token)
    title = ""
    filecount = 0
    if meta:
        # API 偶发返回 HTML 实体（如 Foster&#039;s），必须反转义，否则目录名超长被截断后
        # 可能以空格结尾，触发 Windows「建得了目录、写不了文件」的 ENOENT
        title = html_lib.unescape(
            (meta.get("title_jpn") or meta.get("title") or "").strip()
        )
        try:
            filecount = int(meta.get("filecount") or 0)
        except (TypeError, ValueError):
            filecount = 0
        if log and title:
            log(f"API 标题：{title[:80]}", "ok")
        if log and filecount:
            log(f"API 页数：{filecount}", "info")

    # 拉取图库首页 HTML
    r = session.get(base, timeout=30)
    r.raise_for_status()
    html = r.text
    if "exhentai.org" in host and ("sadpanda" in html.lower() or len(html) < 500):
        raise RuntimeError(
            "exhentai 需要有效登录 Cookie（sad panda）。\n"
            "请用浏览器登录 exhentai.org 后导出 cookies.txt。"
        )
    if "Content Warning" in html or "Offensive For Everyone" in html:
        # 跳过内容警告
        r = session.get(base + "?nw=session", timeout=30)
        r.raise_for_status()
        html = r.text

    if not title:
        tm = _EH_TITLE_RE.search(html)
        if tm:
            title = re.sub(r"<[^>]+>", "", tm.group(1)).strip()
            title = html_lib.unescape(title)
    if not filecount:
        fm = _EH_FILECOUNT_RE.search(html) or _EH_FILECOUNT_RE2.search(html)
        if fm:
            try:
                filecount = int(fm.group(1))
            except ValueError:
                pass

    title = title or f"eh_{gid}"
    if log:
        log(f"图库：{title[:100]}", "ok")
    if callable(title_callback):
        try:
            title_callback(title)
        except Exception:
            pass

    # 过滤非法页码
    if selective and filecount:
        bad = sorted(n for n in only if n > filecount)
        only = {n for n in only if 1 <= n <= filecount}
        if bad and log:
            log(f"忽略超出总页数的序号：{bad[:8]}{'…' if len(bad) > 8 else ''}", "warn")
        if not only:
            raise RuntimeError("定点页码均无效（超出图库页数或为空）")

    show_by_page = {}  # 1-based page_num -> show_url
    visited_thumb = set()

    def _ingest_thumb_html(html_text: str, *, keep_all: bool) -> int:
        """吞入一页缩略图 HTML。返回本页阅读链条数（未过滤）。"""
        ordered, by_page = _eh_collect_show_links(html_text, host=host)
        if keep_all:
            for u in ordered:
                pn = _eh_show_page_num(u)
                if pn > 0:
                    show_by_page.setdefault(pn, u)
                else:
                    # 无页码时按出现顺序兜底（极少见）
                    pass
        else:
            for pn, u in by_page.items():
                if pn in only:
                    show_by_page.setdefault(pn, u)
        return len(ordered)

    # ── 收集阅读页链接 ─────────────────────────────────────────────
    if not selective:
        # 全量：逐翻缩略图分页
        page_i = 0
        while True:
            if cancel_flag and cancel_flag[0]:
                raise Exception("用户取消")
            page_url = base if page_i == 0 else f"{base}?p={page_i}"
            if page_i > 0:
                r = session.get(page_url, timeout=30)
                r.raise_for_status()
                html = r.text
            found = _ingest_thumb_html(html, keep_all=True)
            visited_thumb.add(page_i)
            if log:
                log(
                    f"  缩略图第 {page_i + 1} 页：+{found} 条阅读链（累计 {len(show_by_page)}）",
                    "inline:thumb",
                )
            if found == 0:
                break
            if filecount and len(show_by_page) >= filecount:
                break
            if f"?p={page_i + 1}" not in html and f"&p={page_i + 1}" not in html:
                if not re.search(rf'href=["\'][^"\']*[?&]p={page_i + 1}["\']', html):
                    break
            page_i += 1
            if page_i > 200:
                break
            time.sleep(0.35)
    else:
        # 定点：先吃首页，估每页缩略图数，再只翻可能含目标的缩略图页
        _ingest_thumb_html(html, keep_all=False)
        # 用未过滤计数估 thumbs_per_page
        ordered_first, _ = _eh_collect_show_links(html, host=host)
        thumbs_per_page = len(ordered_first) or 20
        visited_thumb.add(0)
        if log:
            log(
                f"  缩略图首页：每页约 {thumbs_per_page} 张 · "
                f"已命中 {len(show_by_page)}/{len(only)}",
                "info",
            )

        def _still_needed():
            return sorted(only - set(show_by_page.keys()))

        needed_left = _still_needed()
        # 按目标页码推算缩略图分页（0-based p=）
        guess_pages = sorted({(n - 1) // thumbs_per_page for n in needed_left})
        for tp in guess_pages:
            if cancel_flag and cancel_flag[0]:
                raise Exception("用户取消")
            if tp in visited_thumb:
                continue
            page_url = base if tp == 0 else f"{base}?p={tp}"
            r = session.get(page_url, timeout=30)
            r.raise_for_status()
            html = r.text
            got = _ingest_thumb_html(html, keep_all=False)
            visited_thumb.add(tp)
            # 若本页实际张数与估计不同，后面会走兜底扫描
            if log:
                log(
                    f"  缩略图 p={tp}：本页链 {got} · 命中 {len(show_by_page)}/{len(only)}",
                    "inline:thumb",
                )
            if not _still_needed():
                break
            time.sleep(0.25)

        # 兜底：估计偏差时顺序补翻，直到齐或没有下一页
        needed_left = _still_needed()
        if needed_left:
            if log:
                log(
                    f"  定点未齐 {len(needed_left)} 页，顺序扫描缩略图…",
                    "warn",
                )
            max_thumb = 200
            if filecount and thumbs_per_page:
                max_thumb = min(
                    200, (filecount + thumbs_per_page - 1) // thumbs_per_page + 2
                )
            page_i = 0
            empty_streak = 0
            while needed_left and page_i < max_thumb:
                if cancel_flag and cancel_flag[0]:
                    raise Exception("用户取消")
                if page_i not in visited_thumb:
                    page_url = base if page_i == 0 else f"{base}?p={page_i}"
                    r = session.get(page_url, timeout=30)
                    r.raise_for_status()
                    html = r.text
                    got = _ingest_thumb_html(html, keep_all=False)
                    visited_thumb.add(page_i)
                    if got == 0:
                        empty_streak += 1
                        if empty_streak >= 2:
                            break
                    else:
                        empty_streak = 0
                    if log:
                        log(
                            f"  扫描 p={page_i} · 命中 {len(show_by_page)}/{len(only)}",
                            "inline:thumb",
                        )
                    needed_left = _still_needed()
                    if not needed_left:
                        break
                    # 无下一页
                    if f"?p={page_i + 1}" not in html and f"&p={page_i + 1}" not in html:
                        if not re.search(
                            rf'href=["\'][^"\']*[?&]p={page_i + 1}["\']', html
                        ):
                            # 仍可能跳页，继续试几个
                            pass
                    time.sleep(0.25)
                page_i += 1

        still = _still_needed()
        if still and log:
            prev = "、".join(f"{n:04d}" for n in still[:10])
            more = f" 等{len(still)}个" if len(still) > 10 else ""
            log(f"  缩略图未找到阅读链：{prev}{more}", "warn")

    if not show_by_page:
        raise RuntimeError("图库页未找到阅读链接（可能被拦或 Cookie 无效）")

    # 输出顺序：定点按页码；全量按页码（与 0001… 一致）
    page_nums = sorted(show_by_page.keys())
    show_pairs = [(pn, show_by_page[pn]) for pn in page_nums]

    if log:
        if selective:
            log(
                f"定点：{len(show_pairs)} 个阅读页待提取"
                f"（目标 {len(only)} · 缩略图翻了 {len(visited_thumb)} 页）",
                "ok",
            )
        else:
            log(f"共 {len(show_pairs)} 个阅读页，开始提取原图地址…", "info")

    images = []
    delay = 0.55
    total_show = len(show_pairs)
    for j, (page_num, show_url) in enumerate(show_pairs):
        if cancel_flag and cancel_flag[0]:
            raise Exception("用户取消")
        try:
            rr = session.get(show_url, timeout=30, headers={"Referer": base})
            rr.raise_for_status()
            body = rr.text
            if _EH_SKIP_HATH_RE.search(body):
                raise RuntimeError(
                    "触发 e-hentai 浏览限额 / IP 限制。\n"
                    "请稍后再试，或换网络 / 登录账号提高限额。"
                )
            im = _EH_IMG_ID_RE.search(body) or _EH_IMG_ID_RE2.search(body)
            if not im:
                # 备用：最大的 hath 图
                cand = re.findall(
                    r'src=["\'](https?://[^"\']*hath\.network[^"\']+)["\']',
                    body, re.I,
                )
                img_url = cand[0] if cand else ""
            else:
                img_url = im.group(1)
            if img_url:
                img_url = html_lib.unescape(img_url)
                alts = [
                    html_lib.unescape(u)
                    for u in re.findall(
                        r'src=["\'](https?://[^"\']*hath\.network[^"\']+)["\']',
                        body, re.I,
                    )
                    if html_lib.unescape(u) != img_url
                ]
                images.append({
                    "url": img_url,
                    "url_alts": alts[:6],
                    "referer": show_url,
                    "index": page_num,
                    "name": f"{page_num:04d}",
                })
                if log and (
                    j == 0
                    or j + 1 == total_show
                    or (j + 1) % (1 if total_show <= 30 else 5) == 0
                ):
                    log(
                        f"  已提取 {j + 1}/{total_show}（页 {page_num:04d}）",
                        "inline:extract",
                    )
            else:
                if log:
                    log(f"  第 {page_num} 页未找到原图", "warn")
        except Exception as e:
            if "浏览限额" in str(e) or "IP 限制" in str(e):
                raise
            if log:
                log(f"  第 {page_num} 页失败：{e}", "warn")
        time.sleep(delay)

    if not images:
        raise RuntimeError("未能提取到任何原图地址")

    return {
        "site": host,
        "title": title,
        "source_url": base,
        "images": images,
        "filecount": filecount or (max(show_by_page.keys()) if show_by_page else len(images)),
        "selective": selective,
    }


def parse_generic_page(session, url: str, log=None) -> dict:
    """通用 HTML：抽取 img / data-src / srcset 等图片地址。"""
    if log:
        log("使用通用解析（HTML 图片标签）…", "info")
    r = session.get(url, timeout=30)
    r.raise_for_status()
    # 编码
    r.encoding = r.apparent_encoding or r.encoding or "utf-8"
    html = r.text
    base = r.url

    title = ""
    tm = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if tm:
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", tm.group(1))).strip()
    title = title or urlparse(base).netloc or "page"

    found = []
    seen = set()

    def _add(raw: str):
        if not raw:
            return
        raw = html_lib.unescape(raw.strip())
        if raw.startswith("//"):
            raw = "https:" + raw
        full = urljoin(base, raw)
        if full in seen:
            return
        if not _looks_like_image_url(full):
            return
        # 过滤明显缩略图过小路径（仍保留，很多站 full 也带 thumb 字样）
        seen.add(full)
        found.append(full)

    for pat in _GENERIC_IMG_ATTRS:
        for m in re.finditer(pat, html, re.I):
            _add(m.group(1))

    for m in _SRCSET_RE.finditer(html):
        # 取 srcset 里最大的一档
        parts = [p.strip() for p in m.group(1).split(",") if p.strip()]
        best = ""
        best_w = -1
        for p in parts:
            bits = p.split()
            if not bits:
                continue
            u = bits[0]
            w = 0
            if len(bits) > 1 and bits[1].endswith("w"):
                try:
                    w = int(bits[1][:-1])
                except ValueError:
                    w = 0
            if w >= best_w:
                best_w = w
                best = u
        if best:
            _add(best)

    # 简单排序：优先带 full/original 的
    def _score(u: str) -> tuple:
        low = u.lower()
        s = 0
        if any(k in low for k in ("original", "full", "large", "master", "big")):
            s += 10
        if "thumb" in low or "small" in low or "preview" in low:
            s -= 5
        if _IMG_EXT_RE.search(low):
            s += 2
        return (-s, u)

    found.sort(key=_score)
    images = [
        {"url": u, "referer": base, "index": i + 1, "name": f"{i + 1:04d}"}
        for i, u in enumerate(found)
    ]
    if log:
        log(f"通用解析找到 {len(images)} 条图片地址", "ok" if images else "warn")
    if not images:
        raise RuntimeError(
            "页面上没有识别到图片地址。\n"
            "可能是动态加载（需浏览器）或需要登录 Cookie。"
        )
    return {
        "site": urlparse(base).netloc,
        "title": title,
        "source_url": base,
        "images": images,
        "filecount": len(images),
    }


def parse_gallery_url(
    session,
    url: str,
    log=None,
    cancel_flag=None,
    title_callback=None,
    only_indices=None,
) -> dict:
    url = (url or "").strip()
    if _EH_GALLERY_RE.search(url):
        return parse_eh_gallery(
            session,
            url,
            log=log,
            cancel_flag=cancel_flag,
            title_callback=title_callback,
            only_indices=only_indices,
        )
    # 单页 e-h 阅读链：尝试反查图库较复杂，先当通用
    if _EH_PAGE_RE.search(url):
        if log:
            log("这是单页阅读链接，请粘贴图库页 /g/数字/token/ 以下载整本", "warn")
        # 仍尝试只下这一页原图
        r = session.get(url, timeout=30)
        r.raise_for_status()
        body = r.text
        im = _EH_IMG_ID_RE.search(body) or _EH_IMG_ID_RE2.search(body)
        if not im:
            raise RuntimeError("无法从该阅读页提取原图；请改用图库链接")
        return {
            "site": urlparse(url).netloc,
            "title": f"page_{_EH_PAGE_RE.search(url).group(2)}",
            "source_url": url,
            "images": [{
                "url": html_lib.unescape(im.group(1)),
                "referer": url,
                "index": 1,
                "name": "0001",
            }],
            "filecount": 1,
        }
    return parse_generic_page(session, url, log=log)


def gallery_folder_path(save_dir: str, title: str) -> str:
    return os.path.join(save_dir, _safe_folder_name(title, "gallery"))


def _numeric_name_candidates(stem: str) -> list:
    """0003 / 003 / 3 等补零变体（去重保序）。"""
    stem = os.path.splitext(str(stem or "").strip())[0]
    if not stem:
        return []
    out = [stem]
    if stem.isdigit():
        n = int(stem)
        out.extend((f"{n:05d}", f"{n:04d}", f"{n:03d}", f"{n:02d}", str(n)))
    seen = set()
    uniq = []
    for c in out:
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def image_file_exists(folder: str, name: str) -> bool:
    """目录下是否已有该序号的有效图片文件。

    兼容 0001 / 001 / 1 等不同补零写法，以及非标准扩展名。
    """
    if not folder or not name:
        return False
    stem = os.path.splitext(str(name).strip())[0]
    if not stem:
        return False
    candidates = _numeric_name_candidates(stem)
    cand_set = set(candidates)
    _IMG_EXTS = ("webp", "jpg", "jpeg", "png", "gif", "avif", "bmp", "jfif", "jpe")
    for base in candidates:
        for e in _IMG_EXTS:
            p = os.path.join(folder, f"{base}.{e}")
            try:
                if os.path.isfile(p) and os.path.getsize(p) > 256:
                    return True
            except Exception:
                log.debug("检查已存在文件失败 path=%s", p, exc_info=True)
    # 兜底：任意扩展名，只要 stem 对上且体积有效
    try:
        for fn in os.listdir(folder):
            fstem, _ext = os.path.splitext(fn)
            if fstem not in cand_set:
                continue
            p = os.path.join(folder, fn)
            try:
                if os.path.isfile(p) and os.path.getsize(p) > 256:
                    return True
            except Exception:
                log.debug("检查已存在文件失败 path=%s", p, exc_info=True)
    except Exception:
        pass
    return False


def scan_sequence_missing_names(
    folder: str,
    *,
    expected_total: int = 0,
    ignore_ext: bool = True,
) -> list:
    """与「序列文件检查」同一套规则，扫出当前仍缺的序号名。

    返回如 ['0003', '0007']；扫不到序列时返回空列表。
    """
    folder = (folder or "").strip()
    if not folder or not os.path.isdir(folder):
        return []
    try:
        all_files = os.listdir(folder)
    except Exception:
        return []
    files = [
        f for f in all_files
        if os.path.isfile(os.path.join(folder, f))
        and f != MISSING_REPORT_NAME
        and not f.endswith(".tmp")
        and not f.endswith(".part")
    ]
    groups = {}
    for f in files:
        # 与 image_file_exists 一致：过小/残文件不当成已下
        try:
            if os.path.getsize(os.path.join(folder, f)) <= 256:
                continue
        except Exception:
            continue
        name, ext = os.path.splitext(f)
        m = re.search(r"(\d+)$", name)
        if not m:
            continue
        prefix = name[: m.start()]
        num = int(m.group(1))
        digits = m.group(1)
        key = (prefix.lower(),) if ignore_ext else (prefix.lower(), ext.lower())
        groups.setdefault(key, []).append((num, digits, f))
    if not groups:
        return []
    try:
        expected_total = int(expected_total or 0)
    except (TypeError, ValueError):
        expected_total = 0
    missing_names = []
    for key, items in groups.items():
        items.sort(key=lambda x: x[0])
        nums = [it[0] for it in items]
        num_min, num_max = nums[0], nums[-1]
        zfill = max(len(it[1]) for it in items)
        prefix = key[0] if key else ""
        is_pure_num = (not prefix) or (str(prefix).strip() == "")
        if expected_total > 0 and is_pure_num:
            num_min = 1
            num_max = max(num_max, expected_total)
            zfill = max(zfill, len(str(num_max)), 4)
        for n in sorted(set(range(num_min, num_max + 1)) - set(nums)):
            missing_names.append(f"{prefix}{str(n).zfill(zfill)}")
    return missing_names


def list_missing_images(images: list, folder: str) -> list:
    """返回尚未成功落盘的条目（用于补全下载）。"""
    missing = []
    for it in images or []:
        name = it.get("name") or f"{int(it.get('index') or 0):04d}"
        if not image_file_exists(folder, name):
            missing.append(it)
    return missing


MISSING_REPORT_NAME = "_缺失文件清单.txt"


def write_missing_report(info: dict, folder: str, ok_n: int = 0) -> str:
    """下载未完成时，在目录中放一份统计文档，列出缺失文件及链接。

    返回写入的文件路径；若全部齐全则返回空字符串。"""
    images = (info or {}).get("images") or []
    missing = list_missing_images(images, folder)
    if not missing:
        return ""
    total = len(images)
    title = (info or {}).get("title") or "untitled"
    source = (info or {}).get("source_url") or ""
    lines = [
        "图集下载未完成报告",
        "",
        f"作品：{title}",
        f"链接：{source}",
        f"目录：{folder}",
        "",
        f"应下：{total} 张",
        f"已下：{ok_n} 张",
        "失败：0 张",
        f"缺失：{len(missing)} 张",
        "",
        "── 缺失文件 ──",
    ]
    for it in missing:
        nm = it.get("name") or f"{int(it.get('index') or 0):04d}"
        url = it.get("url") or ""
        lines.append(f"{nm}  {url}")
    lines += [
        "",
        "── 手动补下 ──",
        "重新粘贴上方链接到「图集下载」页 → 点「补全下载」即可只下缺失项",
        "也可将上方文件链接逐个输入浏览器 / 下载工具单独下载",
    ]
    text = "\n".join(lines)
    report_path = os.path.join(folder, MISSING_REPORT_NAME)
    tmp = report_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, report_path)
    if os.path.isfile(report_path):
        return report_path
    return ""


def find_missing_report(path: str) -> str:
    """在目录或直接文件路径中定位 _缺失文件清单.txt；找不到返回空串。"""
    p = (path or "").strip().strip('"')
    if not p:
        return ""
    try:
        if os.path.isfile(p):
            base = os.path.basename(p)
            if base == MISSING_REPORT_NAME or base.endswith(MISSING_REPORT_NAME):
                return os.path.abspath(p)
            # 任意 .txt：若内容像图集未完成报告，也认
            if base.lower().endswith(".txt"):
                try:
                    with open(p, "r", encoding="utf-8", errors="ignore") as f:
                        head = f.read(800)
                    if "图集下载未完成" in head or "e-hentai.org/g/" in head or "exhentai.org/g/" in head:
                        return os.path.abspath(p)
                except Exception:
                    pass
            return ""
        if os.path.isdir(p):
            cand = os.path.join(p, MISSING_REPORT_NAME)
            if os.path.isfile(cand):
                return os.path.abspath(cand)
            # 容错：目录里其它含「缺失」的 txt
            try:
                for name in os.listdir(p):
                    if not name.lower().endswith(".txt"):
                        continue
                    if "缺失" in name or "清单" in name:
                        full = os.path.join(p, name)
                        if os.path.isfile(full):
                            return os.path.abspath(full)
            except Exception:
                pass
    except Exception:
        return ""
    return ""


def extract_eh_gallery_url(text: str) -> str:
    """从任意文本中抽出 e-hentai / exhentai 图库页 URL（不要 hath 直链）。

    支持：
      · 完整 https://e-hentai.org/g/ID/token/
      · 半角/全角「链接：」行
      · 无协议的 e-hentai.org/g/...
    找不到返回空串。
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    # 1) 标准图库路径（优先于文中其它 http 链接）
    m = re.search(
        r"(https?://(?:e-hentai|exhentai)\.org/g/\d+/[0-9a-fA-F]+/?)",
        raw,
        re.I,
    )
    if m:
        return m.group(1).rstrip(").,];'\"")
    # 2) 无协议
    m = re.search(
        r"((?:e-hentai|exhentai)\.org/g/\d+/[0-9a-fA-F]+/?)",
        raw,
        re.I,
    )
    if m:
        return "https://" + m.group(1).rstrip(").,];'\"")
    # 3) 「链接：」后整段再提
    m = re.search(r"链接\s*[:：]\s*(\S+)", raw)
    if m:
        return extract_eh_gallery_url(m.group(1))
    # 4) 退回通用 http 抽取，但必须落在 eh 域
    try:
        u = extract_url_from_text(raw)
        low = u.lower()
        if "e-hentai.org" in low or "exhentai.org" in low:
            # 排除 hath.network 等（不应出现在 extract 的 eh 域分支）
            if "hath.network" in low:
                return ""
            return u
    except Exception:
        pass
    return ""


def parse_missing_report(path: str) -> dict:
    """解析图集生成的 _缺失文件清单.txt，提取作品名与源链接。

    返回 {path, title, source_url, folder, expected_count}；读失败或无效时字段可能为空。
    source_url 优先为 e-hentai 图库页（不用清单里的 hath 直链）。
    expected_count：清单「应下：N」总页数，用于序列检查识别尾部缺失。
    """
    out = {
        "path": "",
        "title": "",
        "source_url": "",
        "folder": "",
        "expected_count": 0,
    }
    p = find_missing_report(path) or (os.path.abspath(path) if path and os.path.isfile(path) else "")
    if not p or not os.path.isfile(p):
        return out
    out["path"] = p
    try:
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except Exception:
        try:
            with open(p, "r", encoding="utf-16", errors="ignore") as f:
                text = f.read()
        except Exception:
            return out
    for raw in (text or "").splitlines():
        line = (raw or "").strip()
        if line.startswith("作品：") or line.startswith("作品:"):
            out["title"] = re.split(r"[:：]", line, 1)[-1].strip()
        elif line.startswith("链接：") or line.startswith("链接:"):
            out["source_url"] = re.split(r"[:：]", line, 1)[-1].strip()
        elif line.startswith("目录：") or line.startswith("目录:"):
            out["folder"] = re.split(r"[:：]", line, 1)[-1].strip()
        elif line.startswith("应下：") or line.startswith("应下:"):
            m = re.search(r"(\d+)", line)
            if m:
                try:
                    out["expected_count"] = int(m.group(1))
                except ValueError:
                    pass
    # 全文再抽一次图库 URL（覆盖「链接」行损坏 / 半角冒号 / 多余字符）
    gallery = extract_eh_gallery_url(text or "")
    if gallery:
        out["source_url"] = gallery
    elif out.get("source_url"):
        gallery = extract_eh_gallery_url(out["source_url"])
        if gallery:
            out["source_url"] = gallery
    if not out.get("expected_count"):
        m = re.search(r"应下\s*[:：]\s*(\d+)", text or "")
        if m:
            try:
                out["expected_count"] = int(m.group(1))
            except ValueError:
                pass
    return out


def parse_missing_report_files(path: str) -> list:
    """从清单「── 缺失文件 ──」段抽出 [(name, url), ...]。"""
    out = []
    p = find_missing_report(path) or (
        os.path.abspath(path) if path and os.path.isfile(path) else ""
    )
    if not p or not os.path.isfile(p):
        return out
    try:
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except Exception:
        try:
            with open(p, "r", encoding="utf-16", errors="ignore") as f:
                text = f.read()
        except Exception:
            return out
    in_sec = False
    for raw in (text or "").splitlines():
        s = (raw or "").strip()
        if s.startswith("──") or s.startswith("--") or s.startswith("——"):
            if "缺失文件" in s:
                in_sec = True
                continue
            if in_sec:
                break
            continue
        if not in_sec or not s:
            continue
        parts = re.split(r"\s+", s, 1)
        nm = (parts[0] or "").strip()
        url = (parts[1] if len(parts) > 1 else "").strip()
        if nm:
            out.append((nm, url))
    # 无「缺失文件」段时：兜底抽「名字 + http 链接」行
    if not out:
        for raw in (text or "").splitlines():
            s = (raw or "").strip()
            if not s or s.startswith("#") or s.startswith("──") or s.startswith("--"):
                continue
            m = re.match(r"^(\S+)\s+(https?://\S+)", s, re.I)
            if not m:
                continue
            nm = (m.group(1) or "").strip()
            url = (m.group(2) or "").strip()
            if nm:
                out.append((nm, url))
    return out


def sync_missing_report(
    folder: str,
    still_items,
    *,
    report_path: str = "",
    title: str = "",
    source_url: str = "",
    expected_count: int = 0,
    ok_n: int = 0,
    fail_n: int = 0,
) -> str:
    """补全收口：已齐则删除 _缺失文件清单.txt；未齐则按仍缺名单重写。

    still_items：名字字符串，或 (name, url) / {name, url}。
    返回：删除后空串；更新成功为清单路径。
    """
    folder = (folder or "").strip()
    path = (report_path or "").strip()
    if not path and folder:
        path = os.path.join(folder, MISSING_REPORT_NAME)

    rows = []
    seen = set()
    for it in still_items or []:
        if isinstance(it, dict):
            nm = str(it.get("name") or "").strip()
            url = str(it.get("url") or "").strip()
        elif isinstance(it, (tuple, list)):
            nm = str(it[0] if it else "").strip()
            url = str(it[1] if len(it) > 1 else "").strip()
        else:
            nm = str(it or "").strip()
            url = ""
        if not nm:
            continue
        key = os.path.splitext(nm)[0].lower()
        if key in seen:
            continue
        seen.add(key)
        rows.append((nm, url))

    if not rows:
        if path and os.path.isfile(path):
            os.remove(path)
        return ""

    meta = parse_missing_report(path) if path and os.path.isfile(path) else {}
    title = (title or "").strip() or meta.get("title") or "untitled"
    source = (source_url or "").strip() or meta.get("source_url") or ""
    expected = int(expected_count or 0) or int(meta.get("expected_count") or 0)
    if expected <= 0:
        expected = max(0, int(ok_n or 0)) + len(rows)
    if int(ok_n or 0) <= 0 and expected:
        ok_n = max(0, expected - len(rows))
    try:
        fail_n = int(fail_n or 0)
    except (TypeError, ValueError):
        fail_n = 0
    if fail_n <= 0:
        fail_n = len(rows)
    dest_folder = folder or meta.get("folder") or ""
    lines = [
        "图集下载未完成报告",
        "",
        f"作品：{title}",
        f"链接：{source}",
        f"目录：{dest_folder}",
        "",
        f"应下：{expected} 张",
        f"已下：{int(ok_n or 0)} 张",
        f"失败：{fail_n} 张",
        f"缺失：{len(rows)} 张",
        "",
        "── 缺失文件 ──",
    ]
    for nm, url in rows:
        lines.append(f"{nm}  {url}".rstrip())
    lines += [
        "",
        "── 手动补下 ──",
        "重新粘贴上方链接到「图集下载」页 → 点「补全下载」即可只下缺失项",
        "也可将上方文件链接逐个输入浏览器 / 下载工具单独下载",
    ]
    dest = path or os.path.join(dest_folder, MISSING_REPORT_NAME)
    text = "\n".join(lines)
    tmp = dest + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, dest)
    except Exception:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except Exception:
            pass
        # Windows 上目标文件被占用时 replace 会失败，退回直接覆盖
        with open(dest, "w", encoding="utf-8") as f:
            f.write(text)
    return dest if os.path.isfile(dest) else ""


def collect_fill_still_rows(
    folder: str,
    planned_names,
    *,
    report_path: str = "",
    info: dict = None,
    last_failed=None,
    expected_count: int = 0,
) -> list:
    """按盘面统计补全后仍缺项，附带尽量找回的直链。返回 [(name, url), ...]。

    来源：本次计划名 + 旧清单 + 本次失败项 + 序列重扫（与「序列文件检查」一致）。
    已落盘的从名单里剔除，保证补全失败项会写回 _缺失文件清单.txt。
    """
    folder = (folder or "").strip()
    rows = []
    seen = set()

    def _add_cand(nm):
        nm = str(nm or "").strip()
        if not nm:
            return
        key = os.path.splitext(nm)[0].lower()
        if key in seen:
            return
        seen.add(key)
        rows.append(nm)

    for miss in planned_names or []:
        _add_cand(miss)
    rp = (report_path or "").strip()
    old_urls = {}
    if rp and os.path.isfile(rp):
        for nm, url in parse_missing_report_files(rp):
            _add_cand(nm)
            if url:
                old_urls[os.path.splitext(nm)[0].lower()] = url
                old_urls[nm] = url
    for it in last_failed or []:
        _add_cand((it or {}).get("name"))

    def _lookup_url(miss: str) -> str:
        stem = os.path.splitext(miss)[0]
        keys = {miss, stem}
        # 本次失败项 / 解析结果带的是刷新后的直链，优先于旧清单
        for src in (last_failed or [], (info or {}).get("images") or []):
            for it in src:
                iname = str((it or {}).get("name") or "")
                if fill_name_matches(iname, keys):
                    u = str((it or {}).get("url") or "")
                    if u:
                        return u
        return old_urls.get(stem.lower()) or old_urls.get(miss) or ""

    still = []
    still_keys = set()
    for miss in rows:
        stem = os.path.splitext(miss)[0]
        if folder and (
            image_file_exists(folder, stem) or image_file_exists(folder, miss)
        ):
            continue
        key = stem.lower()
        if key in still_keys:
            continue
        still_keys.add(key)
        still.append((miss, _lookup_url(miss)))

    # 序列重扫：补全后盘面上仍缺的序号必须进清单（与「指定文件夹」检查口径一致）
    if folder and os.path.isdir(folder):
        exp = int(expected_count or 0)
        if exp <= 0 and rp and os.path.isfile(rp):
            try:
                exp = int((parse_missing_report(rp) or {}).get("expected_count") or 0)
            except Exception:
                exp = 0
        for nm in scan_sequence_missing_names(folder, expected_total=exp):
            key = os.path.splitext(nm)[0].lower()
            if key in still_keys:
                continue
            still_keys.add(key)
            still.append((nm, _lookup_url(nm)))
    return still


def fill_name_matches(card_name: str, missing_names) -> bool:
    """序列检查缺失名 ↔ 图源卡 name 是否对应（精确或尾部编号）。"""
    n = (card_name or "").strip()
    if not n:
        return False
    names = {str(x).strip() for x in (missing_names or []) if str(x).strip()}
    if not names:
        return False
    if n in names:
        return True
    # 去扩展名再比
    n_stem = os.path.splitext(n)[0]
    if n_stem in names:
        return True
    m = re.search(r"(\d+)$", n_stem)
    if not m:
        return False
    num = int(m.group(1))
    for miss in names:
        ms = os.path.splitext(miss)[0]
        if ms == n_stem:
            return True
        mm = re.search(r"(\d+)$", ms)
        if mm and int(mm.group(1)) == num:
            return True
    return False


def _eh_extract_img_urls_from_show_html(body: str) -> list:
    """从阅读页 HTML 抽出原图直链（主图 + 其它 hath）。去重保序。"""
    out = []
    seen = set()

    def _add(u: str):
        u = html_lib.unescape((u or "").strip())
        if not u or u in seen:
            return
        if not u.startswith("http"):
            return
        seen.add(u)
        out.append(u)

    im = _EH_IMG_ID_RE.search(body or "") or _EH_IMG_ID_RE2.search(body or "")
    if im:
        _add(im.group(1))
    for u in re.findall(
        r'src=["\'](https?://[^"\']*hath\.network[^"\']+)["\']',
        body or "",
        re.I,
    ):
        _add(u)
    return out


def _eh_nl_href_from_show_html(body: str, show_url: str = "") -> str:
    """阅读页「图片加载失败点这里」→ 换 H@H 节点的 nl 链接。"""
    body = body or ""
    m = re.search(
        r'id=["\']loadfail["\'][^>]*href=["\']([^"\']+)["\']',
        body,
        re.I,
    )
    if not m:
        m = re.search(
            r'href=["\']([^"\']*[?&]nl=[^"\']+)["\'][^>]*>\s*Click here if',
            body,
            re.I,
        )
    if not m:
        m = re.search(r'href=["\']([^"\']*[?&]nl=[0-9a-fA-F\-]+[^"\']*)["\']', body, re.I)
    if not m:
        return ""
    href = html_lib.unescape(m.group(1).strip())
    if href.startswith("http"):
        return href
    base = show_url or "https://e-hentai.org/"
    return urljoin(base, href)


def refresh_image_url(
    session, item: dict, gallery_url: str = "", *, rotate_nl: bool = False
) -> str:
    """从阅读页 referer 重新抓原图直链（hath keystamp 过期 / SSL 坏节点时必须）。

    rotate_nl=True 时优先走 loadfail?nl= 换一台 H@H（SSL 断连常见于坏节点）。
    同时把备选直链写入 item['url_alts']。
    """
    referer = (item or {}).get("referer") or ""
    if not referer:
        return (item or {}).get("url") or ""
    headers = {}
    if gallery_url:
        headers["Referer"] = gallery_url
    else:
        try:
            p = urlparse(referer)
            headers["Referer"] = f"{p.scheme}://{p.netloc}/"
        except Exception:
            pass

    page_url = referer
    # 换节点：先读阅读页拿 nl，再请求 nl 页
    if rotate_nl:
        try:
            r0 = session.get(referer, timeout=30, headers=headers or None)
            r0.raise_for_status()
            if _EH_SKIP_HATH_RE.search(r0.text or ""):
                raise RuntimeError("触发 e-hentai 浏览限额 / IP 限制")
            nl = _eh_nl_href_from_show_html(r0.text or "", referer)
            if nl:
                page_url = nl
        except RuntimeError:
            raise
        except Exception:
            page_url = referer

    r = session.get(page_url, timeout=30, headers=headers or None)
    r.raise_for_status()
    body = r.text or ""
    if _EH_SKIP_HATH_RE.search(body):
        raise RuntimeError("触发 e-hentai 浏览限额 / IP 限制")

    urls = _eh_extract_img_urls_from_show_html(body)
    # 若本页仍是旧节点、且尚未 rotate，再试一次 nl
    if not urls and not rotate_nl:
        nl = _eh_nl_href_from_show_html(body, referer)
        if nl:
            r2 = session.get(nl, timeout=30, headers=headers or None)
            r2.raise_for_status()
            body2 = r2.text or ""
            if _EH_SKIP_HATH_RE.search(body2):
                raise RuntimeError("触发 e-hentai 浏览限额 / IP 限制")
            urls = _eh_extract_img_urls_from_show_html(body2)
            page_url = nl

    if not urls:
        return (item or {}).get("url") or ""

    old = (item or {}).get("url") or ""
    # 优先选与旧链不同的 hath 主机（真正换节点）
    primary = urls[0]
    if old and "hath.network" in old:
        try:
            old_host = urlparse(old).netloc
            for u in urls:
                if urlparse(u).netloc != old_host:
                    primary = u
                    break
        except Exception:
            pass

    alts = [u for u in urls if u != primary]
    if old and old not in alts and old != primary:
        alts.append(old)
    try:
        item["url"] = primary
        item["url_alts"] = alts
        # 若走了 nl 页，referer 仍用原阅读页（CDN 校验更稳）
        if item.get("referer"):
            pass
    except Exception:
        pass
    return primary


def _system_proxy_for_curl() -> str:
    """取得 requests/urllib 会自动使用的系统代理地址，供独立的 curl 子进程使用。

    requests 的 Session 默认 trust_env=True：会读取 HTTP_PROXY/HTTPS_PROXY 等
    环境变量，Windows 下在环境变量为空时还会进一步读取注册表里「Internet 选项」
    的系统代理（很多代理软件的「系统代理」模式就是写这里）。但用 subprocess
    单独拉起的 curl 进程不会自动读取这些，必须显式通过 -x 传入，否则在需要代理
    才能访问 pixiv 的网络下会出现「用 requests 解析成功、用 curl 下载却失败」。
    """
    try:
        import urllib.request
        proxies = urllib.request.getproxies() or {}
        return proxies.get("https") or proxies.get("http") or ""
    except Exception:
        return ""


def download_one_image(
    session,
    url: str,
    dest_base: str,
    *,
    referer: str = "",
    cancel_flag=None,
    timeout=(20, 120),
) -> str:
    """流式下载一张图。dest_base 为无扩展名路径前缀（…/0001）。返回最终文件路径。"""
    # pixiv CDN (i.pximg.net)：requests 库 TLS 指纹会被拦截，用系统 curl
    if referer and "pximg.net" in url:
        parent = os.path.dirname(dest_base)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)

        # requests/urllib 默认会自动读取系统代理（环境变量，或 Windows 下
        # 「Internet 选项」里通过代理软件写入注册表的系统代理），所以解析阶段
        # （走 requests 请求 pixiv 的 AJAX 接口）在需要代理才能访问 pixiv 的网络
        # 环境下也能成功。但这里下载图片改用独立的 curl 子进程，curl **不会**
        # 自动继承这个系统代理，必须显式传入，否则会出现「能解析、但图片连不上」
        # 的现象（浏览器因为也用了系统代理所以链接能打开，程序却下载失败/超时）。
        proxy_url = _system_proxy_for_curl()

        # 先试直连（curl 原生 TLS 指纹，更接近浏览器）
        for attempt in range(2):  # 直连 → 镜像
            target = url if attempt == 0 else url.replace("i.pximg.net", "i.pixiv.re", 1)
            try:
                import subprocess
                import shutil
                curl_exe = shutil.which("curl") or shutil.which("curl.exe") or "curl"
                # curl 参数：-L 跟随重定向 -o 输出文件 -H 自定义头 -sS 静默但显示错误
                # -w 把最终 HTTP 状态码打印到 stdout，用来判断 403/404 等错误
                # （curl 默认即使收到 403/404 也会把错误页正文写进 -o 的文件、
                # 且退出码仍是 0，不加这个判断会把错误页当成图片保存下来）
                tmp_path = dest_base + ".part"
                cmd = [curl_exe, "-L", "-o", tmp_path,
                       "-H", f"User-Agent: {_UA}",
                       "-H", f"Referer: {referer}",
                       "-H", "Accept: image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                       "--connect-timeout", "20", "--max-time", "120",
                       "-w", "\n%{http_code}", "-sS"]
                if proxy_url:
                    cmd += ["-x", proxy_url]
                cmd.append(target)
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=130,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                http_code = ""
                if proc.stdout:
                    lines = proc.stdout.strip().splitlines()
                    if lines:
                        http_code = lines[-1].strip()
                if proc.returncode != 0 or not os.path.isfile(tmp_path):
                    stderr = (proc.stderr or "").strip()
                    hint = ""
                    if proc.returncode == 7:
                        hint = "（无法连接，若当前网络访问 pixiv 需要代理，请确认系统代理已开启）"
                    elif proc.returncode in (28, 35):
                        hint = "（连接超时，可能需要代理才能访问 pixiv）"
                    if stderr:
                        raise RuntimeError(f"curl 返回 {proc.returncode}: {stderr[:200]}{hint}")
                    raise RuntimeError(f"curl 返回 {proc.returncode}，文件未创建{hint}")
                if http_code and http_code.isdigit() and int(http_code) >= 400:
                    if os.path.isfile(tmp_path):
                        os.remove(tmp_path)
                    if int(http_code) in (403, 404) and attempt == 0:
                        continue  # 被拦截/过期 → 试镜像
                    raise RuntimeError(f"服务器返回 HTTP {http_code}")
                if os.path.getsize(tmp_path) < 64:
                    with open(tmp_path, "rb") as _f:
                        head = _f.read(200)
                    os.remove(tmp_path)
                    if b"cloudflare" in head.lower() or b"<html" in head.lower() or b"<!DOCTYPE" in head:
                        continue  # 被 Cloudflare/403 拦截 → 下一方案
                    raise RuntimeError("文件过小，可能不是图片")
                # 确定扩展名
                real_ext = _guess_ext_from_file(tmp_path)
                if not real_ext:
                    real_ext = _guess_ext(url, "")
                dest = f"{dest_base}.{real_ext}"
                os.replace(tmp_path, dest)
                return dest
            except Exception as e:
                if attempt == 0:
                    continue  # 直连失败 → 试镜像
                extra = "\n（提示：若当前网络访问 pixiv 需要代理/VPN，请确认代理软件已开启系统代理或设置了 HTTPS_PROXY 环境变量）" if not proxy_url else ""
                raise RuntimeError(f"下载失败：{e}\nURL: {url}{extra}")

    # 通用下载（e-hentai / hath 等）
    headers = {"Referer": referer} if referer else {}
    # 父目录必须存在；否则 Windows 上 open(.part) 只会报含糊的 ENOENT
    parent = os.path.dirname(dest_base)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)

    def _save_stream(sess, target_url: str) -> str:
        with sess.get(
            target_url, headers=headers, stream=True, timeout=timeout
        ) as resp:
            resp.raise_for_status()
            ct = resp.headers.get("Content-Type", "")
            real_ext = _guess_ext(target_url, ct)
            dest = f"{dest_base}.{real_ext}"
            tmp = dest + ".part"
            try:
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_content(256 * 1024):
                        if cancel_flag and cancel_flag[0]:
                            raise Exception("用户取消")
                        if chunk:
                            f.write(chunk)
                if os.path.getsize(tmp) < 64:
                    raise RuntimeError("文件过小，可能不是图片")
                # 拒绝 HTML 错误页
                with open(tmp, "rb") as rf:
                    head = rf.read(200)
                if b"<html" in head.lower() or b"<!doctype" in head.lower():
                    raise RuntimeError("服务器返回 HTML，不是图片")
                os.replace(tmp, dest)
                return dest
            finally:
                if os.path.isfile(tmp):
                    try:
                        os.remove(tmp)
                    except Exception:
                        pass

    def _save_via_curl(target_url: str) -> str:
        """hath SSL 异常时用系统 curl 兜底（部分 Windows OpenSSL 与节点不对付）。"""
        import subprocess
        import shutil as _shutil

        curl_exe = _shutil.which("curl") or _shutil.which("curl.exe") or "curl"
        real_ext = _guess_ext(target_url, "")
        dest = f"{dest_base}.{real_ext}"
        tmp = dest + ".part"
        cmd = [
            curl_exe, "-L", "-o", tmp,
            "-H", f"User-Agent: {_UA}",
            "-H", f"Referer: {referer or 'https://e-hentai.org/'}",
            "-H", "Accept: image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "--connect-timeout", "25", "--max-time", "180",
            "--retry", "2", "--retry-delay", "1",
            "-w", "\n%{http_code}", "-sS",
            target_url,
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=200,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        http_code = ""
        if proc.stdout:
            lines = proc.stdout.strip().splitlines()
            if lines:
                http_code = lines[-1].strip()
        if proc.returncode != 0 or not os.path.isfile(tmp):
            err = (proc.stderr or "").strip()[:200]
            raise RuntimeError(f"curl 失败 code={proc.returncode} {err}")
        if http_code.isdigit() and int(http_code) >= 400:
            try:
                os.remove(tmp)
            except Exception:
                pass
            raise RuntimeError(f"curl HTTP {http_code}")
        if os.path.getsize(tmp) < 64:
            try:
                os.remove(tmp)
            except Exception:
                pass
            raise RuntimeError("curl 文件过小")
        # 按内容修正扩展名
        real_ext2 = _guess_ext_from_file(tmp) or real_ext
        dest2 = f"{dest_base}.{real_ext2}"
        if dest2 != dest and os.path.isfile(dest):
            try:
                os.remove(dest)
            except Exception:
                pass
        os.replace(tmp, dest2)
        return dest2

    is_hath = "hath.network" in (url or "").lower()
    # hath 节点 SSL 断连频繁：requests 多试 2 次，再 curl
    attempts = 3 if is_hath else 1
    last_err = None
    for i in range(attempts):
        if cancel_flag and cancel_flag[0]:
            raise Exception("用户取消")
        try:
            return _save_stream(session, url)
        except Exception as e:
            last_err = e
            if "用户取消" in str(e):
                raise
            # SSL / 连接类错误才多轮；HTTP 4xx 直接失败
            msg = str(e).lower()
            retryable = any(
                k in msg
                for k in (
                    "ssl", "eof", "connection", "reset", "timed out", "timeout",
                    "broken pipe", "10054", "unexpected_eof",
                )
            )
            if not retryable or i + 1 >= attempts:
                break
            time.sleep(0.4 + 0.6 * i)

    if is_hath:
        try:
            return _save_via_curl(url)
        except Exception as ce:
            last_err = ce if last_err is None else last_err
            raise RuntimeError(f"{last_err}；curl 兜底亦失败：{ce}") from ce
    if last_err:
        raise last_err
    raise RuntimeError("下载失败")


# ── 后台线程 ─────────────────────────────────────────────────────────────────

class GalleryParseWorker(QThread):
    ok = pyqtSignal(dict)
    err = pyqtSignal(str)
    log = pyqtSignal(str, str)
    title_ready = pyqtSignal(str)

    def __init__(
        self,
        url_text: str,
        cookie_path: str = "",
        cancel_flag=None,
        only_indices=None,
    ):
        super().__init__()
        self.url_text = url_text
        self.cookie_path = (cookie_path or "").strip()
        self._cancel = cancel_flag if cancel_flag is not None else [False]
        # 补全定点：只解析这些 1-based 页码（None=全本）
        self.only_indices = only_indices

    def run(self):
        if not HAS_REQUESTS:
            self.err.emit("未安装 requests\n\n请执行：pip install requests")
            return
        try:
            url = extract_url_from_text(self.url_text)
        except Exception as e:
            self.err.emit(str(e))
            return
        self.log.emit(f"链接：{url}", "info")
        only = _eh_normalize_only_indices(self.only_indices)
        if only:
            prev = "、".join(f"{n:04d}" for n in sorted(only)[:10])
            more = f" 等{len(only)}页" if len(only) > 10 else ""
            self.log.emit(f"定点解析：{prev}{more}（跳过已有页）", "ok")
        try:
            session = _make_session(self.cookie_path)
            if self.cookie_path and os.path.isfile(self.cookie_path):
                self.log.emit("已加载 Cookie 文件", "ok")
            else:
                self.log.emit("未使用 Cookie（公开图库一般可用；exhentai 必须登录）", "info")

            def _lg(msg, level="info"):
                self.log.emit(msg, level)

            def _on_title(title_str: str):
                self.title_ready.emit(title_str)

            info = parse_gallery_url(
                session, url, log=_lg, cancel_flag=self._cancel,
                title_callback=_on_title,
                only_indices=only or None,
            )
            if self._cancel[0]:
                self.err.emit("已取消")
                return
            n = len(info.get("images") or [])
            if info.get("selective"):
                self.log.emit(
                    f"定点解析完成：{info.get('title', '')[:60]} · 提取 {n} 张",
                    "ok",
                )
            else:
                self.log.emit(
                    f"解析完成：{info.get('title', '')[:60]} · {n} 张",
                    "ok",
                )
            self.ok.emit(info)
        except Exception as e:
            if self._cancel[0] or "用户取消" in str(e):
                self.err.emit("已取消")
            else:
                self.err.emit(str(e).strip() or repr(e))


class PixivParseWorker(QThread):
    ok = pyqtSignal(dict)
    err = pyqtSignal(str)
    log = pyqtSignal(str, str)

    def __init__(self, url_text: str, cookie_path: str = "", cancel_flag=None):
        super().__init__()
        self.url_text = url_text
        self.cookie_path = (cookie_path or "").strip()
        self._cancel = cancel_flag if cancel_flag is not None else [False]

    def run(self):
        if not HAS_REQUESTS:
            self.err.emit("未安装 requests\n\n请执行：pip install requests")
            return
        try:
            url = extract_url_from_text(self.url_text)
        except Exception as e:
            self.err.emit(str(e))
            return
        self.log.emit(f"链接：{url}", "info")
        try:
            session = _make_session(self.cookie_path)
            if self.cookie_path and os.path.isfile(self.cookie_path):
                self.log.emit("已加载 Cookie 文件", "ok")
            else:
                self.log.emit("未使用 Cookie（pixiv 通常需要登录）", "info")

            info = parse_pixiv_page(
                session, url,
                log=lambda msg, level="info": self.log.emit(msg, level),
                cancel_flag=self._cancel,
            )
            if self._cancel[0]:
                self.err.emit("已取消")
                return
            n = len(info.get("images") or [])
            self.log.emit(
                f"解析完成：{info.get('title', '')[:60]} · {n} 张",
                "ok",
            )
            self.ok.emit(info)
        except Exception as e:
            if self._cancel[0] or "用户取消" in str(e):
                self.err.emit("已取消")
            else:
                self.err.emit(str(e).strip() or repr(e))


class HitomiParseWorker(QThread):
    ok = pyqtSignal(dict)
    err = pyqtSignal(str)
    log = pyqtSignal(str, str)
    title_ready = pyqtSignal(str)

    def __init__(self, url_text: str, cancel_flag=None):
        super().__init__()
        self.url_text = url_text
        self._cancel = cancel_flag if cancel_flag is not None else [False]

    def run(self):
        if not HAS_REQUESTS:
            self.err.emit("未安装 requests\n\n请执行：pip install requests")
            return
        try:
            gid = extract_hitomi_id(self.url_text)
        except Exception as e:
            self.err.emit(str(e))
            return
        self.log.emit(f"图库 ID：{gid}（无需 Cookie · 官网 Download 同款 zip）", "info")
        try:
            session = _make_session("")
            info = parse_hitomi_gallery(
                session,
                gid,
                log=lambda msg, level="info": self.log.emit(msg, level),
                cancel_flag=self._cancel,
                title_callback=lambda t: self.title_ready.emit(t),
            )
            if self._cancel[0]:
                self.err.emit("已取消")
                return
            n = len(info.get("images") or [])
            self.log.emit(
                f"解析完成：{info.get('title', '')[:60]} · {n} 张",
                "ok",
            )
            self.ok.emit(info)
        except Exception as e:
            if self._cancel[0] or "用户取消" in str(e):
                self.err.emit("已取消")
            else:
                self.err.emit(str(e).strip() or repr(e))


class HitomiDebugWorker(QThread):
    """后台跑 debug_hitomi_gallery，避免卡 UI。"""
    done = pyqtSignal(object)

    def __init__(self, url_text: str, sample: int = 8, parent=None):
        super().__init__(parent)
        self._url = url_text
        self._sample = sample

    def run(self):
        try:
            rep = debug_hitomi_gallery(self._url, sample=self._sample)
            self.done.emit(rep)
        except Exception as ex:
            self.done.emit({"ok": False, "error": str(ex), "report_path": ""})


class HitomiZipDownloadWorker(QThread):
    """hitomi.la：对齐官网 Download 按钮 —— 逐张拉 webp 后打成一个 .zip。

    实测（tools/hitomi_zip_debug.py）：
      · 页面 #dl-button → download.js → JSZip + FileSaver
      · 串行 XHR webp（throttle≈1s）→ 浏览器本地生成 title.zip
      · /download/{id}.zip 等服务端整包 URL 全部 404
    因此「单一 ZIP」只能复刻客户端流程，不能一次 GET 拿整包。
    输出：{save_dir}/{title}.zip
    """

    progress = pyqtSignal(int, str)
    ok = pyqtSignal(str)  # zip 路径或所在目录
    err = pyqtSignal(str)
    log = pyqtSignal(str, str)
    done_detail = pyqtSignal(dict)
    item_done = pyqtSignal(str)
    finished_clean = pyqtSignal()

    def __init__(
        self,
        images: list,
        save_dir: str,
        title: str = "",
        delay_ms: int = 1000,
        skip_existing: bool = True,
        cancel_flag=None,
        *,
        retries: int = 3,
        mode: str = "download",
    ):
        super().__init__()
        self.images = list(images or [])
        self.save_dir = save_dir
        self.title = title or "hitomi"
        self.delay_ms = max(0, int(delay_ms if delay_ms is not None else 1000))
        self.skip_existing = bool(skip_existing)
        self._cancel = cancel_flag if cancel_flag is not None else [False]
        self.retries = max(1, int(retries))
        self.mode = mode or "download"

    def _interruptible_sleep(self, ms: int) -> bool:
        if ms <= 0:
            return bool(self._cancel[0])
        waited = 0
        step = 50
        while waited < ms:
            if self._cancel[0]:
                return True
            time.sleep(step / 1000.0)
            waited += step
        return bool(self._cancel[0])

    @staticmethod
    def _is_webp(data: bytes) -> bool:
        # RIFF....WEBP
        return (
            isinstance(data, (bytes, bytearray))
            and len(data) >= 12
            and data[:4] == b"RIFF"
            and data[8:12] == b"WEBP"
        )

    def _fetch_webp(self, session, url: str, referer: str) -> bytes:
        headers = {
            "Referer": referer or "https://hitomi.la/",
            "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
        }
        r = session.get(url, headers=headers, timeout=(20, 120))
        r.raise_for_status()
        data = r.content or b""
        ct = (r.headers.get("Content-Type") or "").lower()
        if b"<html" in data[:200].lower() or "text/html" in ct:
            raise RuntimeError("CDN 返回 HTML（直链可能过期或缺少 Referer）")
        if len(data) < 64:
            raise RuntimeError("文件过小，可能不是图片")
        if not self._is_webp(data):
            # 偶发仍是 avif/jpeg；允许非 webp 但拒绝 HTML
            if data[:3] in (b"\xff\xd8\xff",) or data[:8] == b"\x89PNG\r\n\x1a\n":
                return bytes(data)
            if data[:4] == b"RIFF":
                return bytes(data)
            raise RuntimeError(f"非图片内容（content-type={ct or '?'}）")
        return bytes(data)

    def run(self):
        import zipfile

        if not HAS_REQUESTS:
            self.err.emit("未安装 requests")
            self.finished_clean.emit()
            return
        if not self.images:
            self.err.emit("没有可下载的图片")
            self.finished_clean.emit()
            return

        save_dir = (self.save_dir or "").strip() or os.path.join(
            os.path.expanduser("~/Downloads"), "hitomi.la"
        )
        try:
            os.makedirs(save_dir, exist_ok=True)
        except Exception as e:
            self.err.emit(f"无法创建目录：{e}")
            self.finished_clean.emit()
            return

        zip_base = _safe_folder_name(self.title, "hitomi")
        zip_path = os.path.join(save_dir, f"{zip_base}.zip")

        if self.skip_existing and os.path.isfile(zip_path) and os.path.getsize(zip_path) > 256:
            self.log.emit(f"已存在 zip，跳过：{zip_path}", "ok")
            for it in self.images:
                try:
                    self.item_done.emit(str(it.get("name") or ""))
                except Exception:
                    pass
            self.progress.emit(100, "已存在 ✓")
            self.done_detail.emit({
                "folder": save_dir,
                "zip_path": zip_path,
                "ok_n": len(self.images),
                "skip_n": len(self.images),
                "fail_n": 0,
                "failed": [],
                "mode": self.mode,
            })
            self.ok.emit(zip_path)
            self.finished_clean.emit()
            return

        self.log.emit(
            f"官网同款打包：共 {len(self.images)} 张 webp → {zip_path}",
            "info",
        )
        # 下载前再刷一次 gg，并按最新 b 重算直链（防止解析后 b 轮换）
        session = _make_session("")
        try:
            cases, default_m, case_m, b = _hitomi_load_gg(session, force=True)
            for it in self.images:
                h = (it.get("hash") or "").strip()
                if h:
                    it["url"] = _hitomi_webp_url(h, cases, default_m, case_m, b)
            self.log.emit(
                f"直链已按最新 gg 刷新 · m=({default_m}/{case_m}) · b={b}",
                "ok",
            )
        except Exception as e:
            self.log.emit(f"刷新 gg.js 失败（沿用解析时直链）：{e}", "warn")

        total = len(self.images)
        ok_n = 0
        fail_n = 0
        failed = []
        # 先写到内存/临时 zip，成功后再替换正式文件
        tmp_zip = zip_path + ".part"
        try:
            if os.path.isfile(tmp_zip):
                try:
                    os.remove(tmp_zip)
                except Exception:
                    pass
            with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_STORED) as zf:
                used_names = set()
                for i, item in enumerate(self.images):
                    if self._cancel[0]:
                        self.err.emit("已取消")
                        self.finished_clean.emit()
                        return
                    item = dict(item)
                    name = item.get("name") or f"{i + 1:04d}"
                    zip_name = (item.get("zip_name") or f"{name}.webp").replace("\\", "/")
                    # zip 内重名处理
                    base_zn = zip_name
                    n = 1
                    while zip_name.lower() in used_names:
                        stem, ext = os.path.splitext(base_zn)
                        zip_name = f"{stem}_{n}{ext or '.webp'}"
                        n += 1
                    used_names.add(zip_name.lower())

                    pct = int(i * 100 / max(1, total))
                    self.progress.emit(pct, f"打包 {i + 1}/{total}")

                    url = item.get("url") or ""
                    referer = item.get("referer") or "https://hitomi.la/"
                    last_err = None
                    data = None
                    for attempt in range(1, self.retries + 1):
                        if self._cancel[0]:
                            self.err.emit("已取消")
                            self.finished_clean.emit()
                            return
                        try:
                            if attempt > 1:
                                self.log.emit(
                                    f"  ↻ {name} 第 {attempt}/{self.retries} 次重试…",
                                    "info",
                                )
                                # 重试时强制刷 gg 重算
                                try:
                                    cases, default_m, case_m, b = _hitomi_load_gg(
                                        session, force=True
                                    )
                                    h = (item.get("hash") or "").strip()
                                    if h:
                                        url = _hitomi_webp_url(
                                            h, cases, default_m, case_m, b
                                        )
                                        item["url"] = url
                                except Exception:
                                    pass
                                if self._interruptible_sleep(min(2000, 500 * attempt)):
                                    self.err.emit("已取消")
                                    self.finished_clean.emit()
                                    return
                            if not url:
                                raise RuntimeError("无图片地址")
                            data = self._fetch_webp(session, url, referer)
                            last_err = None
                            break
                        except Exception as e:
                            last_err = e
                            data = None
                            if "用户取消" in str(e):
                                self.err.emit("已取消")
                                self.finished_clean.emit()
                                return

                    if data is not None:
                        # 按内容修正 zip 内扩展名
                        if self._is_webp(data):
                            if not zip_name.lower().endswith(".webp"):
                                zip_name = os.path.splitext(zip_name)[0] + ".webp"
                        zf.writestr(zip_name, data)
                        ok_n += 1
                        try:
                            self.item_done.emit(name)
                        except Exception:
                            pass
                        if (i + 1) % 5 == 0 or i + 1 == total:
                            self.log.emit(
                                f"  ✓ {i + 1}/{total}  {zip_name}  ({len(data)} B)",
                                "ok",
                            )
                    else:
                        fail_n += 1
                        failed.append(item)
                        self.log.emit(f"  ✗ {name}：{last_err}", "warn")

                    # 官网 throttle 约 1s；沿用页内间隔
                    if self.delay_ms and i + 1 < total:
                        if self._interruptible_sleep(self.delay_ms):
                            self.err.emit("已取消")
                            self.finished_clean.emit()
                            return

            if ok_n == 0:
                try:
                    if os.path.isfile(tmp_zip):
                        os.remove(tmp_zip)
                except Exception:
                    pass
                self.err.emit("全部下载失败，未生成 zip")
                self.finished_clean.emit()
                return

            # 原子替换
            try:
                if os.path.isfile(zip_path):
                    os.remove(zip_path)
            except Exception:
                pass
            os.replace(tmp_zip, zip_path)

            self.progress.emit(100, "完成 ✓")
            detail = {
                "folder": save_dir,
                "zip_path": zip_path,
                "ok_n": ok_n,
                "skip_n": 0,
                "fail_n": fail_n,
                "failed": failed,
                "mode": self.mode,
            }
            self.done_detail.emit(detail)
            self.log.emit(
                f"打包完成：成功 {ok_n} · 失败 {fail_n} · {zip_path}",
                "ok" if fail_n == 0 else "warn",
            )
            if fail_n and failed:
                names = ", ".join((it.get("name") or "?") for it in failed[:12])
                more = f" 等 {len(failed)} 张" if len(failed) > 12 else ""
                self.log.emit(f"失败序号：{names}{more}", "warn")
            self.ok.emit(zip_path)
        except Exception as e:
            try:
                if os.path.isfile(tmp_zip):
                    os.remove(tmp_zip)
            except Exception:
                pass
            if self._cancel[0] or "用户取消" in str(e):
                self.err.emit("已取消")
            else:
                self.err.emit(str(e))
        finally:
            self.finished_clean.emit()


class GalleryDownloadWorker(QThread):
    progress = pyqtSignal(int, str)  # 0-100, status
    ok = pyqtSignal(str)             # 目录
    err = pyqtSignal(str)
    log = pyqtSignal(str, str)
    # {folder, ok_n, skip_n, fail_n, failed: [item,...], mode}
    done_detail = pyqtSignal(dict)
    # 单张成功落盘后通知 UI 把对应内容卡变灰（name 如 0001）
    item_done = pyqtSignal(str)
    finished_clean = pyqtSignal()

    def __init__(
        self,
        images: list,
        save_dir: str,
        title: str = "",
        cookie_path: str = "",
        delay_ms: int = 1200,
        skip_existing: bool = True,
        cancel_flag=None,
        *,
        retries: int = 3,
        refresh_url: bool = True,
        gallery_url: str = "",
        mode: str = "download",  # download | fill
        folder_override: str = "",
    ):
        super().__init__()
        self.images = list(images or [])
        self.save_dir = save_dir
        self.title = title or "gallery"
        self.cookie_path = (cookie_path or "").strip()
        self.delay_ms = max(0, int(delay_ms))
        self.skip_existing = bool(skip_existing)
        self._cancel = cancel_flag if cancel_flag is not None else [False]
        self.retries = max(1, int(retries))
        self.refresh_url = bool(refresh_url)
        self.gallery_url = gallery_url or ""
        self.mode = mode or "download"
        # 序列补全：强制写入用户指定的已有作品目录（不经 title 再拼一层）
        self.folder_override = (folder_override or "").strip()

    def _interruptible_sleep(self, ms: int) -> bool:
        """返回 True 表示被取消。"""
        if ms <= 0:
            return bool(self._cancel[0])
        waited = 0
        step = 50
        while waited < ms:
            if self._cancel[0]:
                return True
            time.sleep(step / 1000.0)
            waited += step
        return bool(self._cancel[0])

    def run(self):
        if not HAS_REQUESTS:
            self.err.emit("未安装 requests")
            self.finished_clean.emit()
            return
        if not self.images:
            self.err.emit("没有可下载的图片")
            self.finished_clean.emit()
            return
        if self.folder_override:
            folder = os.path.abspath(self.folder_override)
        else:
            folder = gallery_folder_path(self.save_dir, self.title)
        try:
            os.makedirs(folder, exist_ok=True)
            # 兜底：Windows 可能剥掉路径段尾部空格/点，用真实目录名回写，避免后续 open ENOENT
            parent, leaf = os.path.dirname(folder), os.path.basename(folder)
            if parent and leaf and os.path.isdir(parent):
                try:
                    for ent in os.listdir(parent):
                        if ent.rstrip(" .") == leaf.rstrip(" .") and ent != leaf:
                            alt = os.path.join(parent, ent)
                            if os.path.isdir(alt):
                                folder = alt
                                break
                except Exception:
                    pass
            # 再确认可写（尽早暴露路径问题）
            if not os.path.isdir(folder):
                raise OSError(f"目录创建后不存在：{folder}")
            probe = os.path.join(folder, ".write_test")
            with open(probe, "wb") as _pf:
                _pf.write(b"ok")
            try:
                os.remove(probe)
            except Exception:
                pass
        except Exception as e:
            self.err.emit(f"无法创建目录：{e}")
            self.finished_clean.emit()
            return

        tag = "补全" if self.mode == "fill" else "下载"
        self.log.emit(f"{tag}保存到：{folder} · 共 {len(self.images)} 项 · 每张最多重试 {self.retries} 次", "info")
        session = _make_session(self.cookie_path)
        total = len(self.images)
        ok_n = 0
        skip_n = 0
        fail_n = 0
        failed = []

        try:
            for i, item in enumerate(self.images):
                if self._cancel[0]:
                    self.err.emit("已取消")
                    self.finished_clean.emit()
                    return

                # 拷贝一份，避免污染原列表时仍可回写 url
                item = dict(item)
                url = item.get("url") or ""
                referer = item.get("referer") or ""
                name = item.get("name") or f"{int(item.get('index') or i + 1):04d}"
                item["name"] = name
                pct = int(i * 100 / max(1, total))
                self.progress.emit(pct, f"{tag} {i + 1}/{total}")

                if self.skip_existing and image_file_exists(folder, name):
                    skip_n += 1
                    ok_n += 1
                    try:
                        self.item_done.emit(name)
                    except Exception:
                        pass
                    continue

                dest_base = os.path.join(folder, name)
                # 强制重下：先清掉同序号旧文件，避免 .webp/.jpg 残留多份
                if not self.skip_existing and image_file_exists(folder, name):
                    for e in ("webp", "jpg", "jpeg", "png", "gif", "avif", "bmp"):
                        p = dest_base + "." + e
                        try:
                            if os.path.isfile(p):
                                os.remove(p)
                        except Exception:
                            pass
                last_err = None
                saved = ""
                # 补全模式多给几次机会（hath SSL 断连很常见）
                max_try = max(int(self.retries or 3), 5 if self.mode == "fill" else 3)

                for attempt in range(1, max_try + 1):
                    if self._cancel[0]:
                        self.err.emit("已取消")
                        self.finished_clean.emit()
                        return
                    try:
                        # 第 2 次起：刷新直链；第 3 次起走 nl 换 H@H 节点
                        if attempt > 1 and self.refresh_url and referer:
                            use_nl = attempt >= 3 or (
                                last_err is not None
                                and any(
                                    k in str(last_err).lower()
                                    for k in ("ssl", "eof", "connection", "reset")
                                )
                            )
                            self.log.emit(
                                f"  ↻ {name} 第 {attempt}/{max_try} 次："
                                + ("换 H@H 节点并" if use_nl else "")
                                + "重新拉取阅读页直链…",
                                "info",
                            )
                            try:
                                fresh = refresh_image_url(
                                    session,
                                    item,
                                    self.gallery_url,
                                    rotate_nl=use_nl,
                                )
                                if fresh:
                                    url = fresh
                                    item["url"] = fresh
                            except Exception as re_e:
                                self.log.emit(
                                    f"  刷新直链失败（仍用旧链）：{re_e}",
                                    "warn",
                                )
                            # SSL 后多等一会再打下一节点
                            wait_ms = 1200 if use_nl else 800
                            wait_ms = min(4000, wait_ms + 400 * (attempt - 1))
                            if self._interruptible_sleep(wait_ms):
                                self.err.emit("已取消")
                                self.finished_clean.emit()
                                return
                        elif attempt > 1:
                            self.log.emit(
                                f"  ↻ {name} 第 {attempt}/{max_try} 次重试…",
                                "info",
                            )
                            if self._interruptible_sleep(min(2500, 600 * attempt)):
                                self.err.emit("已取消")
                                self.finished_clean.emit()
                                return

                        if not url:
                            raise RuntimeError("无图片地址")

                        candidates = [url] + [
                            u for u in (item.get("url_alts") or [])
                            if u and u != url
                        ]
                        # 去重保序
                        seen_c = set()
                        uniq_c = []
                        for c in candidates:
                            if c and c not in seen_c:
                                seen_c.add(c)
                                uniq_c.append(c)
                        candidates = uniq_c

                        last_try_err = None
                        saved = ""
                        for cand in candidates:
                            try:
                                saved = download_one_image(
                                    session, cand, dest_base,
                                    referer=referer,
                                    cancel_flag=self._cancel,
                                    timeout=(25, 150),
                                )
                                if cand != url:
                                    item["url"] = cand
                                    url = cand
                                last_try_err = None
                                break
                            except Exception as ce:
                                last_try_err = ce
                                if "用户取消" in str(ce):
                                    raise
                                continue
                        if last_try_err is not None and not saved:
                            raise last_try_err
                        last_err = None
                        break
                    except Exception as e:
                        last_err = e
                        if "用户取消" in str(e):
                            self.err.emit("已取消")
                            self.finished_clean.emit()
                            return
                        # 清掉半成品 .part
                        try:
                            import glob as _glob
                            for p in _glob.glob(dest_base + "*.part"):
                                try:
                                    os.remove(p)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        if attempt >= max_try:
                            break
                        # 间歇性 SSL：换新 Session 避免坏连接复用
                        try:
                            err_l = str(e).lower()
                            if any(k in err_l for k in ("ssl", "eof", "connection")):
                                session = _make_session(self.cookie_path)
                        except Exception:
                            pass

                if last_err is None and saved:
                    ok_n += 1
                    try:
                        self.item_done.emit(name)
                    except Exception:
                        pass
                    if self.mode == "fill" or (i + 1) % 5 == 0 or i + 1 == total:
                        self.log.emit(
                            f"  ✓ {i + 1}/{total}  {os.path.basename(saved)}",
                            "ok",
                        )
                else:
                    fail_n += 1
                    failed.append(item)
                    self.log.emit(f"  ✗ {name}：{last_err}", "warn")

                if self.delay_ms and i + 1 < total:
                    if self._interruptible_sleep(self.delay_ms):
                        self.err.emit("已取消")
                        self.finished_clean.emit()
                        return

            self.progress.emit(100, "完成 ✓")
            detail = {
                "folder": folder,
                "ok_n": ok_n,
                "skip_n": skip_n,
                "fail_n": fail_n,
                "failed": failed,
                "mode": self.mode,
            }
            self.done_detail.emit(detail)
            self.log.emit(
                f"{tag}完成：成功 {ok_n}（含跳过 {skip_n}）· 失败 {fail_n} · 目录 {folder}",
                "ok" if fail_n == 0 else "warn",
            )
            if fail_n and failed:
                names = ", ".join(
                    (it.get("name") or "?") for it in failed[:12]
                )
                more = f" 等 {len(failed)} 张" if len(failed) > 12 else ""
                self.log.emit(
                    f"失败序号：{names}{more} → 可点「补全下载」再试",
                    "warn",
                )
            if ok_n == 0 and fail_n > 0:
                self.err.emit("全部下载失败")
            elif ok_n > 0:
                self.ok.emit(folder)
            elif skip_n == total:
                self.ok.emit(folder)
        except Exception as e:
            if self._cancel[0] or "用户取消" in str(e):
                self.err.emit("已取消")
            else:
                self.err.emit(str(e))
        finally:
            self.finished_clean.emit()


# ── UI ───────────────────────────────────────────────────────────────────────

class GalleryNameCard(QFrame):
    """图源内容卡：仅图名、无缩略图。

    交互约定（与「选中下载」队列一致）：
    · 未下载 / 失败：始终亮起，固定进队，点击无效（不能点灭）
    · 已下载：默认灰色；点击亮起=要重下，再点恢复灰色
    尺寸约视频 MediaCard 的 1/4 量级。
    """
    CARD_W = 55
    CARD_H = 34

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.item = dict(item or {})
        self._name = str(self.item.get("name") or self.item.get("index") or "?")
        self._downloaded = False
        self._failed = False
        # 选中=亮起，参与「选中下载」
        self._selected = True
        self.setObjectName("GalleryNameCard")
        self.setFixedSize(self.CARD_W, self.CARD_H)
        self.setAttribute(Qt.WA_StyledBackground, True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(0)
        self.lbl = QLabel(self._name)
        self.lbl.setAlignment(Qt.AlignCenter)
        self.lbl.setWordWrap(False)
        lay.addWidget(self.lbl)
        self.refresh_theme()

    def is_selected(self) -> bool:
        return bool(self._selected)

    def set_selected(self, yes: bool):
        # 未下载（含失败）不可熄灭：始终亮起、始终可被「选中下载」
        if not self._downloaded:
            yes = True
        self._selected = bool(yes)
        self.refresh_theme()

    def set_downloaded(self, yes: bool, *, keep_selected: bool = False):
        """标记已下载。默认取消选中（变灰）；keep_selected 时保持当前选中。"""
        self._downloaded = bool(yes)
        if yes:
            self._failed = False
            if not keep_selected:
                self._selected = False
        else:
            # 未下载：强制亮起，保证进「选中下载」队列
            self._selected = True
        self.refresh_theme()

    def set_failed(self, yes: bool):
        self._failed = bool(yes)
        if yes:
            # 失败=未到手：保持亮起，点「选中下载」即可重试（不可点灭）
            self._downloaded = False
            self._selected = True
        self.refresh_theme()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            if not self._downloaded:
                self._open_gallery_folder()
            e.accept()
            return
        super().mousePressEvent(e)

    def _open_gallery_folder(self):
        w = self.parent()
        while w is not None:
            if hasattr(w, "_gallery_dir"):
                try:
                    folder = w._gallery_dir()
                    if folder and os.path.isdir(folder):
                        os.startfile(folder)
                except Exception:
                    pass
                return
            w = w.parent()

    def _notify_parent_selection(self):
        """灰卡点选后刷新页头「选中数」与「选中下载」按钮。"""
        w = self.parent()
        while w is not None:
            if hasattr(w, "_sync_card_selection_ui"):
                try:
                    w._sync_card_selection_ui()
                except Exception:
                    pass
                return
            w = w.parent()

    def refresh_theme(self, *_):
        # 优先级：选中亮起 > 失败提示 > 已下载灰色 > 未选中暗淡
        if self._selected:
            bg = tk("panel")
            fg = tk("text_strong")
            bd = tk("accent")
            weight = "700"
        elif self._failed:
            bg = tk("panel_2")
            fg = tk("warn")
            bd = tk("warn")
            weight = "600"
        elif self._downloaded:
            bg = tk("panel_3")
            fg = tk("text_faint")
            bd = tk("border_soft")
            weight = "400"
        else:
            bg = tk("panel_3")
            fg = tk("text_dim")
            bd = tk("border_soft")
            weight = "400"
        # 仅已下载灰卡可点切换；未下载用箭头光标，避免「能点」的错觉
        self.setCursor(Qt.PointingHandCursor if not self._downloaded else Qt.ArrowCursor)
        self.setStyleSheet(
            f"QFrame#GalleryNameCard{{"
            f"background:{bg}; border:1px solid {bd}; border-radius:6px;}}"
        )
        self.lbl.setStyleSheet(
            f"color:{fg}; font-size:11px; font-weight:{weight};"
            f" background:transparent; border:none;"
        )


# ── 图集「运行日志」跟底 ────────────────────────────────────────────────────
# 批处理（尤其 e-hentai 逐图打日志）时，QTextEdit 富文本 append 后滚动条
# maximum 往往晚一拍才更新。旧逻辑用「当前是否在底部」决定是否跟底：
# 一旦 setValue 钉到了过期的 maximum，下一行就会判成「用户上翻了」并永久停滚。
# 改为显式 stick 标志 + 写入时 guard，仅用户手动滚动才取消跟底。

def _gallery_log_bind(page, log_box: QTextEdit) -> None:
    """初始化跟底状态，并监听滚动条（用户手动上翻时取消 stick）。"""
    page._log_stick_bottom = True
    page._log_scroll_guard = False
    page._log_scroll_fix_timer = None
    try:
        log_box.verticalScrollBar().valueChanged.connect(
            lambda v, p=page: _gallery_log_on_scroll(p, v)
        )
    except Exception:
        pass


def _gallery_log_on_scroll(page, value: int) -> None:
    if getattr(page, "_log_scroll_guard", False):
        return
    box = getattr(page, "log_box", None)
    if box is None:
        return
    try:
        sb = box.verticalScrollBar()
        page._log_stick_bottom = int(value) >= max(0, sb.maximum() - 8)
    except Exception:
        pass


def _gallery_log_clear(page) -> None:
    box = getattr(page, "log_box", None)
    if box is not None:
        box.clear()
    page._inline_blocks = {}
    page._log_stick_bottom = True


def _gallery_log_should_follow(page) -> bool:
    if bool(getattr(page, "_log_stick_bottom", True)):
        return True
    box = getattr(page, "log_box", None)
    if box is None:
        return True
    try:
        sb = box.verticalScrollBar()
        if sb.value() >= max(0, sb.maximum() - 8):
            page._log_stick_bottom = True
            return True
    except Exception:
        pass
    return False


def _gallery_log_scroll_bottom(page) -> None:
    """立刻钉底 + 合并一次 0ms 延迟钉底（等文档布局刷新 maximum）。"""
    page._log_stick_bottom = True
    _gallery_log_scroll_bottom_now(page)
    t = getattr(page, "_log_scroll_fix_timer", None)
    if t is None:
        t = QTimer(page)
        t.setSingleShot(True)
        t.timeout.connect(lambda p=page: _gallery_log_scroll_bottom_now(p))
        page._log_scroll_fix_timer = t
    # 高频日志时反复 restart，只在本轮事件循环空闲后再钉一次
    t.start(0)


def _gallery_log_scroll_bottom_now(page) -> None:
    if not bool(getattr(page, "_log_stick_bottom", True)):
        return
    box = getattr(page, "log_box", None)
    if box is None:
        return
    page._log_scroll_guard = True
    try:
        cursor = box.textCursor()
        cursor.movePosition(QTextCursor.End)
        box.setTextCursor(cursor)
        box.ensureCursorVisible()
        sb = box.verticalScrollBar()
        sb.setValue(sb.maximum())
    except Exception:
        pass
    finally:
        page._log_scroll_guard = False


def _gallery_log_append(page, msg, level="info") -> None:
    """写入运行日志；默认跟底，用户上翻后不强拉。"""
    box = getattr(page, "log_box", None)
    if box is None:
        return

    inline_key = None
    if isinstance(level, str) and level.startswith("inline:"):
        inline_key = level[7:]
        level = "info"

    colors = {
        "ok": tk("ok"), "err": tk("err"),
        "warn": tk("warn"), "info": tk("text_mut"),
        "blue": tk("info"),
    }
    color = colors.get(level, tk("text_mut"))
    ts = time.strftime("%H:%M:%S")
    html = (
        f'<span style="color:{tk("text_faint")}">[{ts}]</span> '
        f'<span style="color:{color}">{msg}</span>'
    )

    should_follow = _gallery_log_should_follow(page)
    if not hasattr(page, "_inline_blocks") or page._inline_blocks is None:
        page._inline_blocks = {}

    page._log_scroll_guard = True
    try:
        if inline_key:
            if inline_key in page._inline_blocks:
                block_num = page._inline_blocks[inline_key]
                block = box.document().findBlockByNumber(block_num)
                if block.isValid():
                    cursor = QTextCursor(block)
                    cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
                    cursor.insertHtml(html)
                else:
                    box.append(html)
                    page._inline_blocks[inline_key] = box.document().lastBlock().blockNumber()
            else:
                box.append(html)
                page._inline_blocks[inline_key] = box.document().lastBlock().blockNumber()
        else:
            page._inline_blocks.clear()
            box.append(html)
    finally:
        page._log_scroll_guard = False

    if should_follow:
        _gallery_log_scroll_bottom(page)


_RECORD_SKIP_YELLOW = "#eab308"
_RECORD_SKIP_TIP = (
    "开：读取本页已下记录，命中则跳过重复下载\n"
    "关：不查记录，正常解析并下载；磁盘已有文件仍会提示覆盖/改名/中止"
)


def _record_skip_widgets(page) -> list:
    """页签「防」+ 页底滑动开关（同一状态）。"""
    out = []
    for attr in ("sw_record_skip", "sw_record_skip_bar"):
        w = getattr(page, attr, None)
        if w is not None:
            out.append(w)
    return out


def _record_skip_enabled(page) -> bool:
    """防重开关：开=按已下记录跳过；关=不查记录。无开关时默认开。"""
    for w in _record_skip_widgets(page):
        try:
            return bool(w.isChecked())
        except Exception:
            continue
    return True


def _gallery_set_record_skip(page, on: bool) -> None:
    on = bool(on)
    for w in _record_skip_widgets(page):
        try:
            if bool(w.isChecked()) != on:
                w.setChecked(on)
        except Exception:
            pass
    _gallery_apply_record_hint(page)


def _should_skip_by_record(page, name: str, *, note_continue: bool = True) -> bool:
    """命中 gallery_eh.txt / gallery_hitomi.txt 且开关开启 → 跳过下载。

    关开关或序列补全（_bypass_record）时不跳过。
    关开关但记录命中时，note_continue=True 会打一条「继续」日志。
    """
    if getattr(page, "_bypass_record", False):
        return False
    key = (getattr(page, "_record_key", "") or "").strip()
    label = (name or "").strip()
    if not key or not label:
        return False
    if not has_record(key, label):
        return False
    if not _record_skip_enabled(page):
        if note_continue:
            try:
                page._log(f"记录中已有「{label}」，防重已关，继续", "warn")
            except Exception:
                pass
        return False
    return True


def _gallery_on_record_skip_toggled(page, src=None):
    if src is not None:
        try:
            on = bool(src.isChecked())
        except Exception:
            on = _record_skip_enabled(page)
    else:
        on = _record_skip_enabled(page)
    _gallery_set_record_skip(page, on)
    try:
        page._log(
            "已下记录防重：" + ("开（命中则跳过）" if on else "关（不查记录，继续下载）"),
            "info",
        )
    except Exception:
        pass


def _gallery_normalize_added_names(added_names) -> list:
    if not added_names:
        return []
    if isinstance(added_names, str):
        added_names = [added_names]
    out = []
    for x in added_names:
        s = str(x or "").strip()
        if s:
            out.append(s)
    return out


def _gallery_record_hint_text(page, added_names=None) -> str:
    key = (getattr(page, "_record_key", "") or "").strip()
    n = 0
    if key:
        try:
            n = len(load_records(key))
        except Exception:
            n = 0
    head = f"已有 {n}条"
    if not _record_skip_enabled(page):
        head += " · 防重关"
    names = _gallery_normalize_added_names(added_names)
    if not names:
        return head
    tail = "  ".join(f"添加 {name}" for name in names)
    return f"{head}  {tail}"


def _gallery_canonical_record_name(page) -> str:
    """只记解析标题本名：EH 目录名，hitomi 为 Title.zip。不加 _02 / (2)。"""
    key = (getattr(page, "_record_key", "") or "").strip()
    if not key:
        return ""
    info = getattr(page, "_info", None) or {}
    raw = (info.get("title") or "").strip()
    if not raw:
        return ""
    if key == "gallery_hitomi":
        leaf = _safe_folder_name(raw, "hitomi")
        return leaf if leaf.lower().endswith(".zip") else f"{leaf}.zip"
    return _safe_folder_name(raw, "gallery")


def _gallery_commit_download_record(page) -> list:
    """下载结束后只写入本名。已有则跳过。"""
    key = (getattr(page, "_record_key", "") or "").strip()
    if not key:
        return []
    name = _gallery_canonical_record_name(page)
    if not name:
        return []
    added = []
    if add_record(key, name):
        added.append(name)
        try:
            page._log(f"防重记录已更新：{name}", "ok")
        except Exception:
            pass
        try:
            page._refresh_record_count(added)
        except Exception:
            pass
    else:
        try:
            page._refresh_record_count()
        except Exception:
            pass
    return added


def _gallery_apply_record_hint(page, added_names=None):
    lbl = getattr(page, "lbl_record_hint", None) or getattr(page, "lbl_record_count", None)
    if lbl is None:
        return
    try:
        text = _gallery_record_hint_text(page, added_names)
        if hasattr(lbl, "setFullText"):
            lbl.setFullText(text)
        else:
            lbl.setText(text)
        lbl.setToolTip(text)
    except Exception:
        pass


def _gallery_reload_record_editor(page, editor):
    if editor is None:
        return
    key = (getattr(page, "_record_key", "") or "").strip()
    lines = load_records(key) if key else []
    text = "\n".join(lines)
    if text:
        text += "\n"
    editor.setPlainText(text)


def _open_gallery_records_dialog(page):
    """已下记录弹窗：样式对齐速存「记录文件」。"""
    key = (getattr(page, "_record_key", "") or "").strip()
    if not key:
        return

    dlg = QDialog(page)
    dlg.setWindowTitle("已下记录")
    try:
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
    except Exception:
        pass
    dlg.resize(640, 360)
    dlg.setMinimumSize(480, 280)
    try:
        if page is not None and page.styleSheet():
            dlg.setStyleSheet(page.styleSheet())
    except Exception:
        pass

    v = QVBoxLayout(dlg)
    v.setContentsMargins(12, 12, 12, 12)
    v.setSpacing(10)

    preview = QTextEdit()
    preview.setAcceptRichText(False)
    preview.setStyleSheet(
        f"QTextEdit{{background:{tk('input_bg')};color:{tk('text')};"
        f"border:1px solid {tk('border')};border-radius:6px;font-size:13px;}}"
    )
    _gallery_reload_record_editor(page, preview)
    v.addWidget(preview, 1)

    lbl_total = QLabel("")
    lbl_total.setObjectName("GalleryRecordTotal")
    lbl_total.setStyleSheet(
        f"QLabel#GalleryRecordTotal{{color:{tk('text_mut')};font-size:12px;"
        f"background:transparent;border:none;padding:0 2px;}}"
    )
    v.addWidget(lbl_total)

    def _editor_count() -> int:
        return sum(
            1
            for ln in parse_record_text(preview.toPlainText())
            if not is_record_sep(ln)
        )

    def _set_total_text():
        lbl_total.setText(f"共 {_editor_count()} 条")

    def _on_editor_changed():
        _set_total_text()

    preview.textChanged.connect(_on_editor_changed)
    _set_total_text()

    def _make_btn(text, bg=None, fg=None, bd=None, hover_bg=None):
        b = QPushButton(text)
        b.setFixedHeight(34)
        b.setCursor(Qt.PointingHandCursor)
        if bg:
            qss = (
                f"QPushButton{{background:{bg};color:{fg or '#ffffff'};"
                f"border:1px solid {bd or bg};border-radius:6px;font-size:13px;}}"
            )
            if hover_bg:
                qss += f"QPushButton:hover{{background:{hover_bg};}}"
        else:
            qss = (
                f"QPushButton{{background:{tk('input_bg')};color:{tk('text')};"
                f"border:1px solid {tk('border')};border-radius:6px;font-size:13px;}}"
            )
            if hover_bg:
                qss += f"QPushButton:hover{{background:{hover_bg};}}"
        b.setStyleSheet(qss)
        return b

    def _write_editor_to_file():
        lines = parse_record_text(preview.toPlainText())
        if not save_records(key, lines):
            message_box_warn(dlg, "保存失败", "无法写入已下记录文件。")
            return False
        return True

    def _do_add_dir():
        if hasattr(page, "_on_update_list"):
            try:
                page._on_update_list()
            except Exception:
                log.exception("添加已经下目录失败")
                return
        _gallery_reload_record_editor(page, preview)
        _set_total_text()

    def _scroll_to_sep():
        text = preview.toPlainText()
        idx = text.find("-----")
        if idx < 0:
            return
        cur = preview.textCursor()
        cur.setPosition(idx)
        preview.setTextCursor(cur)
        preview.ensureCursorVisible()
        try:
            cr = preview.cursorRect(cur)
            sb = preview.verticalScrollBar()
            if sb is not None:
                sb.setValue(max(0, sb.value() + cr.top() - 8))
        except Exception:
            pass

    def _do_dedupe():
        # 不自动删：只把 标题_02 / 标题 (2) 挪到 ----- 下，给人眼看
        lines = parse_record_text(preview.toPlainText())
        normal, suspects = split_suspect_record_lines(lines)
        new_text = format_records_with_suspects(normal, suspects)
        preview.blockSignals(True)
        try:
            preview.setPlainText(new_text)
        finally:
            preview.blockSignals(False)
        out_lines = parse_record_text(new_text)
        if not save_records(key, out_lines):
            message_box_warn(dlg, "保存失败", "无法写回记录文件。")
            _set_total_text()
            return
        if hasattr(page, "_refresh_record_count"):
            page._refresh_record_count()
        else:
            _gallery_apply_record_hint(page)
        _set_total_text()
        if suspects:
            QTimer.singleShot(0, _scroll_to_sep)

    def _do_save_close():
        if _write_editor_to_file():
            if hasattr(page, "_refresh_record_count"):
                page._refresh_record_count()
            dlg.accept()

    btn_row = QHBoxLayout()
    btn_row.setSpacing(8)
    btn_add = _make_btn(
        "添加已经下目录",
        bg="#1d4ed8", fg="#dbeafe", bd="#2563eb", hover_bg="#2563eb",
    )
    btn_add.clicked.connect(_do_add_dir)
    btn_row.addWidget(btn_add)

    btn_dedupe = _make_btn(
        "整理记录",
        bg="#a16207", fg="#fef9c3", bd="#ca8a04", hover_bg="#ca8a04",
    )
    btn_dedupe.clicked.connect(_do_dedupe)
    btn_row.addWidget(btn_dedupe)

    btn_row.addStretch(1)

    btn_save = _make_btn(
        "保存并关闭",
        bg="#14532d", fg="#bbf7d0", bd="#166534", hover_bg="#166534",
    )
    btn_save.clicked.connect(_do_save_close)
    btn_row.addWidget(btn_save)

    btn_close = _make_btn("关闭")
    btn_close.clicked.connect(dlg.reject)
    btn_row.addWidget(btn_close)
    v.addLayout(btn_row)

    dlg.exec_()


def _build_gallery_record_bar(page, *, visible: bool = True) -> QWidget:
    """页底一行：左提示（条数 / 加记录反馈）+ 右「已下记录」。"""
    bar = QWidget(page)
    bar.setObjectName("GalleryRecordBar")
    bar.setAttribute(Qt.WA_StyledBackground, True)
    bar.setStyleSheet("#GalleryRecordBar{background:transparent;border:none;}")
    lay = QHBoxLayout(bar)
    lay.setContentsMargins(CARD_LEFT_GAP, 2, CARD_RIGHT_GAP, 2)
    lay.setSpacing(8)

    hint = _ElideLabel("已有 0条")
    hint.setFixedHeight(MEDIUM_BUTTON_H)
    hint.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    hint.setObjectName("GalleryRecordHint")
    hint.setStyleSheet(
        "QLabel#GalleryRecordHint{font-size:12px;color:#94a3b8;padding:0 4px;"
        "background:transparent;border:none;}"
    )
    hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    page.lbl_record_hint = hint
    page.lbl_record_count = hint  # 兼容旧刷新逻辑
    lay.addWidget(hint, 1)

    # 「已下记录」左侧滑动开关，与页签「防」同步
    try:
        from ui_main import SideToggleSwitch
        sw_bar = SideToggleSwitch(on_color=_RECORD_SKIP_YELLOW, parent=bar)
    except Exception:
        log.exception("创建已下记录防重开关失败")
        sw_bar = None
    if sw_bar is not None:
        sw_bar.setChecked(True)
        sw_bar.setToolTip(_RECORD_SKIP_TIP)
        sw_bar.clicked.connect(lambda: _gallery_on_record_skip_toggled(page, sw_bar))
        page.sw_record_skip_bar = sw_bar
        lay.addWidget(sw_bar, 0, Qt.AlignVCenter)
    else:
        page.sw_record_skip_bar = None

    btn = apply_mini_button(QPushButton("已下记录"))
    btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    btn.setMinimumWidth(max(72, btn.sizeHint().width()))
    btn.clicked.connect(page._on_view_file)
    page.btn_records = btn
    page.btn_view_file = btn
    lay.addWidget(btn, 0, Qt.AlignVCenter)

    page._record_bar = bar
    bar.setVisible(bool(visible))
    return bar


class _GalleryEHentaiPage(QWidget):
    """网页图集：解析图源 + 一键下载。（原 PageGallery 主体，现作为「e-hentai.org」分页内容）"""

    # 侧栏「图集下载」下 2px 进度线：active, 0–100, 颜色(与本页进度条 chunk 一致)
    nav_progress = pyqtSignal(bool, int, str)
    # 本页批处理气泡前缀 ZZ（「e-hentai.org批处理 X/Y」）
    _BATCH_LABEL = "e-hentai.org"
    # 自动下载引擎内部 platform key
    _BATCH_ENGINE_KEY = "ehentai"

    def __init__(self):
        super().__init__()
        self._info = None
        self._parse_worker = None
        self._dl_worker = None
        self._cancel = [False]
        self._auto_dl = False
        self._cookie_auto_tried = False   # 本会话是否已尝试自动加载 Cookie
        self._cookie_loaded_via = None    # 'auto' | 'manual' | None
        self._last_failed = []            # 最近一次下载失败的条目
        self._last_download_ok = False   # 供自动下载引擎判断本次下载是否成功
        self._name_cards = []             # [GalleryNameCard, ...]
        self._card_by_name = {}           # name -> GalleryNameCard
        self._nav_busy = False            # 解析或下载中（侧栏进度线可见）
        self._tail_url_text = ""
        self._tail_expected_count = 0
        self._is_placeholder = False      # True：本实例已被冻结为「假页」（见 freeze_as_placeholder）
        self._inline_blocks = {}          # inline_key -> block_number，用于原地更新日志行
        # 序列文件检查 → 补全：只下指定缺失名，写回指定目录，完成后可删清单
        self._fill_only_names = None      # set[str] | None
        self._fill_report_path = ""
        self._gallery_dir_override = ""
        self._bypass_record = False
        self._fill_pending_url = ""
        self._fill_planned_n = 0
        self._fill_gallery_folder_snap = ""
        self._fill_actual_folder = ""
        self._fill_active_once = False
        self._fill_finishing = False
        self._fill_current_i = 0          # 气泡进度 当前项（1-based 进行中）
        self._fill_ok_n = 0
        self._fill_err_n = 0
        self._fill_skip_n = 0
        # 批处理状态
        self._batch_urls = []
        self._batch_index = 0
        self._batch_mode = False
        self._batch_ok = 0
        self._batch_err = 0
        self._batch_skip = 0
        self._batch_file_path = ""
        self._batch_stop_requested = False  # F6：当前条完成后中止
        self._batch_platform_label = self._BATCH_LABEL
        self._record_key = "gallery_eh"
        self._batch_timer = QTimer(self)
        self._batch_timer.setSingleShot(True)
        self._batch_timer.timeout.connect(self._batch_next)

        theme.changed.connect(self.refresh_theme)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        # ══ 解析与下载 ═══════════════════════════════════════════════════════
        gb = make_card("CardGalleryParse", borderless=True)
        gv = QVBoxLayout(gb)
        gv.setSpacing(0)
        gv.setContentsMargins(0, max(0, CARD_TOP_GAP - 6), 0, CARD_BOTTOM_GAP)
        self._theme_titles = []
        self._func_cards = [gb]

        body = QWidget()
        body.setObjectName("GalleryParseBody")
        body.setStyleSheet("#GalleryParseBody{background:transparent;border:none;}")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(7)
        gv.addWidget(body, 1)

        # Cookie / 保存位置控件（由「关于」弹窗统一管理，页面 UI 隐藏）
        self.ck_status = _ElideLabel("")
        self.ck_status.setObjectName("StatusLbl")
        self.ck_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.ck_status.setStyleSheet(f"color:{tk('text_faint')}; font-size:12px;")
        self.ck_status.setVisible(False)
        self.btn_ck = QPushButton("选择文件")
        self.btn_ck.setObjectName("BtnSmall")
        self.btn_ck.setMinimumWidth(72)
        self.btn_ck.clicked.connect(self._pick_cookie)
        self.btn_ck.setVisible(False)
        self.ck_path = QLineEdit()
        self.ck_path.setPlaceholderText("自动扫描下载目录…")
        self.ck_path.setReadOnly(True)
        self.ck_path.setMinimumWidth(0)
        self.ck_path.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._ck_icon = apply_folder_path_edit(self.ck_path)
        self.ck_path.textChanged.connect(self._on_cookie_change)
        self.ck_path.setVisible(False)
        self.save_edit = QLineEdit(
            os.path.join(os.path.expanduser("~"), "Downloads", "Galleries").replace("\\", "/")
        )
        self.save_edit.setMinimumWidth(0)
        self.save_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._sv_icon = apply_folder_path_edit(self.save_edit)
        self.save_edit.setVisible(False)

        # ── 第1行：链接输入 + 取消 ──
        row1 = QHBoxLayout()
        row1.setSpacing(10)

        url_wrap = QWidget()
        url_wrap.setAttribute(Qt.WA_StyledBackground, True)
        url_wrap.setStyleSheet("background: transparent;")
        url_wrap_lay = QVBoxLayout(url_wrap)
        url_wrap_lay.setContentsMargins(0, 0, 0, 0)
        url_wrap_lay.setSpacing(0)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("粘贴 e-hentai 图库链接 → 回车自动解析下载")
        self.url_edit.setFixedHeight(MEDIUM_BUTTON_H)
        self._url_icon = apply_folder_path_edit(self.url_edit)
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
        self.btn_cancel.clicked.connect(lambda: self._cancel.__setitem__(0, True))
        row1.addWidget(self.btn_cancel, 0, Qt.AlignTop)
        bl.addLayout(row1)

        # ── 第2行：提示 ──
        row2 = QHBoxLayout()
        row2.setSpacing(10)

        self.lbl_hint = QLabel("粘贴链接 → 回车自动解析")
        self.lbl_hint.setFixedHeight(MEDIUM_BUTTON_H)
        self.lbl_hint.setAlignment(Qt.AlignVCenter)
        row2.addWidget(self.lbl_hint, 1)
        bl.addLayout(row2)

        root.addWidget(gb, 0)

        # ══ 图源列表（小号内容卡 · 高度约占页面 30%）══════════════════════════
        media_head = QHBoxLayout()
        lbl_media = QLabel("图源列表")
        lbl_media.setObjectName("SecTitle")
        media_head.addWidget(lbl_media)
        media_head.addStretch(1)
        self.chk_hide_done = QCheckBox("隐藏完成")
        self.chk_hide_done.setStyleSheet("background:transparent; font-size:12px;")
        self.chk_hide_done.toggled.connect(self._toggle_hide_done)
        media_head.addWidget(self.chk_hide_done)
        self.btn_sel_dl = QPushButton("选中下载")
        self.btn_sel_dl.setObjectName("BtnSmall")
        self.btn_sel_dl.setMinimumWidth(84)
        self.btn_sel_dl.setEnabled(False)
        self.btn_sel_dl.setStyleSheet("QPushButton{padding:2px 10px;} QPushButton:disabled{color:#555555;background:transparent;border-color:#333333;}")
        self.btn_sel_dl.clicked.connect(self._download_selected)
        media_head.addWidget(self.btn_sel_dl)
        self.btn_open_dir = QPushButton("打开目录")
        self.btn_open_dir.setObjectName("BtnSmall")
        self.btn_open_dir.setMinimumWidth(84)
        self.btn_open_dir.setEnabled(True)
        self.btn_open_dir.setStyleSheet("QPushButton{padding:2px 10px;} QPushButton:disabled{color:#555555;background:transparent;border-color:#333333;}")
        self.btn_open_dir.clicked.connect(self._open_save_dir)
        media_head.addWidget(self.btn_open_dir)
        root.addLayout(media_head)
        self.card_scroll = QScrollArea()
        self.card_scroll.setWidgetResizable(True)
        self.card_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.card_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.card_scroll.setMinimumHeight(GalleryNameCard.CARD_H * 3 + 16)
        self.card_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.card_scroll.setFrameShape(QFrame.NoFrame)
        self.card_scroll.setObjectName("GalleryCardScroll")
        self.card_scroll.viewport().setAutoFillBackground(False)
        self.card_scroll.setStyleSheet(
            "QScrollArea#GalleryCardScroll{background:transparent;border:none;}"
            "QScrollArea#GalleryCardScroll > QWidget > QWidget{background:transparent;}"
        )
        card_inner = QWidget()
        card_inner.setObjectName("GalleryCardInner")
        card_inner.setAutoFillBackground(False)
        card_inner.setStyleSheet(
            "#GalleryCardInner{background:transparent;border:none;}"
        )
        self._card_layout = FlowLayout(card_inner, margin=4, h_spacing=6, v_spacing=6)
        self.card_scroll.setWidget(card_inner)
        # 高度在 resizeEvent 里按页面 30% 设定
        root.addWidget(self.card_scroll, 0)

        self._empty_hint = None
        self._show_empty_cards()

        # ══ 运行日志 ═════════════════════════════════════════════════════════
        card_log = make_card("CardGalleryLog", borderless=True)
        log_lay = QVBoxLayout(card_log)
        log_lay.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        log_lay.setSpacing(0)
        title_log = install_card_title(card_log, log_lay, "运行日志")
        self._theme_titles.append(title_log)
        self._func_cards.append(card_log)

        head = title_log.parentWidget()
        head_l = head.layout() if head else None
        if head_l is not None:
            head_l.removeWidget(title_log)
            title_row = QHBoxLayout()
            title_row.setContentsMargins(0, 0, 0, 0)
            title_row.setSpacing(6)
            title_row.addWidget(title_log, 0, Qt.AlignVCenter)
            title_row.addStretch(1)
            self.btn_clear_log = apply_mini_button(QPushButton("清除记录"))
            self.btn_clear_log.clicked.connect(lambda: _gallery_log_clear(self))
            title_row.addWidget(self.btn_clear_log, 0, Qt.AlignVCenter)
            head_l.addLayout(title_row)

        self.log_box = QTextEdit()
        self.log_box.setObjectName("GalleryLogBox")
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(72)
        self.log_box.setFrameShape(QFrame.NoFrame)
        apply_simple_record(self.log_box)
        _gallery_log_bind(self, self.log_box)
        log_lay.addWidget(self.log_box, 1)
        root.addWidget(card_log, 1)

        # 页底：防重复下载记录
        root.addWidget(_build_gallery_record_bar(self, visible=True), 0)
        self._refresh_record_count()

        if not HAS_REQUESTS:
            QTimer.singleShot(0, lambda: self._log(
                "未检测到 requests，请 pip install requests", "err"
            ))
        # 首帧后按 30% 高度定图源列表
        QTimer.singleShot(0, self._sync_card_strip_height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_card_strip_height()

    def _sync_card_strip_height(self):
        """图源列表区域高度 ≈ 页面高度的 30%。"""
        sc = getattr(self, "card_scroll", None)
        if sc is None:
            return
        h = self.height()
        if h <= 0:
            return
        # 至少 3 行小卡，最多不超过页面一半
        target = int(h * 0.30)
        lo = GalleryNameCard.CARD_H * 3 + 16
        hi = max(lo, int(h * 0.50))
        target = max(lo, min(hi, target))
        if sc.height() != target:
            sc.setFixedHeight(target)
        # 条带高度变化后视口变宽/变窄，需重算内容高度（否则大量卡被裁切）
        self._relayout_name_cards()

    def _relayout_name_cards(self):
        """批量增删内容卡后强制刷新滚动区内容高度。

        FlowLayout 的 minimumSize 只反映单卡，QScrollArea(widgetResizable)
        在仅 addWidget、窗口未 resize 时不会按 heightForWidth 撑开内容，
        结果：只能看到前 2～3 行、无纵向滚动条，表现为「内容卡显示不全」。
        两遍计算：滚动条出现后视口变窄，行数可能增加。
        """
        sc = getattr(self, "card_scroll", None)
        lay = getattr(self, "_card_layout", None)
        if sc is None or lay is None:
            return
        inner = sc.widget()
        if inner is None:
            return
        lay.invalidate()
        for _ in range(2):
            w = max(1, sc.viewport().width() or sc.width() or inner.width() or 1)
            need_h = max(0, int(lay.heightForWidth(w)))
            if inner.minimumHeight() != need_h:
                inner.setMinimumHeight(need_h)
            inner.updateGeometry()

    # ── 主题 / 日志 ──────────────────────────────────────────────────────────


    def refresh_theme(self, *_):
        restyle_folder_path_edit(self.ck_path, getattr(self, "_ck_icon", None))
        restyle_folder_path_edit(self.save_edit, getattr(self, "_sv_icon", None))
        restyle_folder_path_edit(self.url_edit, getattr(self, "_url_icon", None))
        for lbl in self._theme_titles:
            restyle_card_title(lbl)
        for frame in self._func_cards:
            restyle_card_frame(frame)
        for c in getattr(self, "_name_cards", []):
            if hasattr(c, "refresh_theme"):
                c.refresh_theme()
        # Cookie 绿按钮随主题重刷
        path = (self.ck_path.text() or "").strip() if hasattr(self, "ck_path") else ""
        self._set_cookie_btn_loaded(bool(path and os.path.isfile(path)))

    def _log(self, msg, level="info"):
        _gallery_log_append(self, msg, level)

    def _scroll_log_bottom(self):
        _gallery_log_scroll_bottom(self)

    def _project_dir_name(self) -> str:
        """当前作品下载目录名（与落盘文件夹一致，不含路径）。"""
        ov = getattr(self, "_folder_title_override", None)
        raw = str(ov or "").strip() or str((self._info or {}).get("title") or "").strip()
        if not raw:
            return ""
        return _safe_folder_name(raw, "gallery")

    def _with_project(self, text: str) -> str:
        return _append_project_name(text, self._project_dir_name())

    def _set_status(self, text, color=None):
        """状态提示行：下载相关必须醒目，有色时加粗。

        过程中/完成后自动追加项目名（下载目录名），如「已下 2/2 · 无题_17」。
        """
        if hasattr(self, "lbl_hint") and self.lbl_hint is not None:
            body = text or ""
            if _status_needs_project(body):
                body = self._with_project(body)
            if self._batch_mode and self._batch_urls:
                idx = self._batch_index
                total = len(self._batch_urls)
                display = f"进度 {idx}/{total}：" + body
            else:
                display = body
            self.lbl_hint.setText(display)
            col = color or tk("text_mut")
            weight = "700" if color else "600"
            self.lbl_hint.setStyleSheet(
                f"color:{col}; font-weight:{weight}; font-size:13px;"
            )

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

    def _done_ratio_color(self, done_n: int, total: int) -> str:
        """已下/总数 统计色：全完成绿、未开始橙、进行中走进度渐变。"""
        total = max(0, int(total or 0))
        done_n = max(0, int(done_n or 0))
        if total <= 0:
            return tk("text_mut")
        if done_n >= total:
            return tk("ok")
        if done_n <= 0:
            return tk("warn")
        return self._progress_gradient_color(int(done_n * 100 / total))

    def _on_progress_value(self, value):
        # 空闲时不刷新线，避免任务结束后被残留进度值再次点亮
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
        隐藏：必须 active=False（完成/失败/取消/已下载过/无任务）。
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
            # 灰色整线（亮/暗主题都清晰的 slate）
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

        # 保存目录与 Cookie 由「关于」弹窗统一管理，页面 UI 隐藏
        self.btn_ck.setVisible(False)
        self.ck_path.setVisible(False)
        self.ck_status.setVisible(False)
        self.save_edit.setVisible(False)

    # ── Cookie / 目录 ─────────────────────────────────────────────────────────

    def showEvent(self, event):
        """本会话第一次显示本页时：自动扫描下载目录加载 e-hentai Cookie。"""
        super().showEvent(event)
        if self._cookie_auto_tried:
            return
        self._cookie_auto_tried = True
        QTimer.singleShot(0, self._auto_load_cookie)

    # ── 下载间隔：由父级 PageGallery 统一管理 ──

    def _delay_ms(self) -> int:
        if hasattr(self, "_gallery_delay_fn") and self._gallery_delay_fn:
            return self._gallery_delay_fn()
        return 1200

    # ── 用户习惯（records/user.txt · gallery 段）────────────────────────────
    def export_settings(self) -> dict:
        """导出图集页设置，供 user_prefs 落盘。"""
        return {
            "save_path": (self.save_edit.text() or "").strip(),
            "cookie_path": (self.ck_path.text() or "").strip(),
            "check_records": _record_skip_enabled(self),
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
            scan_dir_records("gallery_eh", path)

        ck = (d.get("cookie_path") or "").strip()
        if ck and os.path.isfile(ck):
            self.ck_path.blockSignals(True)
            try:
                self.ck_path.setText(ck.replace("\\", "/"))
            finally:
                self.ck_path.blockSignals(False)
            self._on_cookie_change(ck)
            # 已有合法 Cookie：跳过本会话自动扫描，避免覆盖用户选择
            self._cookie_auto_tried = True
            self._cookie_loaded_via = "manual"

        if "check_records" in d:
            _gallery_set_record_skip(self, bool(d.get("check_records")))

        # 下载间隔在 PageGallery 统一控件上，子页通常无 _set_delay_ms
        if "delay_ms" in d:
            try:
                fn = getattr(self, "_set_delay_ms", None)
                if callable(fn):
                    fn(int(d.get("delay_ms")))
            except Exception:
                pass

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
        """扫描下载目录，优先加载 e-hentai.org_cookies.txt。"""
        cur = (self.ck_path.text() or "").strip()
        if cur and os.path.isfile(cur):
            self._on_cookie_change(cur)
            return

        dirs = []
        if hasattr(self, "save_edit"):
            save_dir = (self.save_edit.text() or "").strip()
            if save_dir:
                dirs.append(save_dir)
                # 若保存位置是 …/Galleries，也扫其父目录（Downloads）
                parent = os.path.dirname(save_dir.rstrip("\\/"))
                if parent:
                    dirs.append(parent)
        for d in _default_download_dirs():
            dirs.append(d)

        path = find_eh_cookie_file(dirs)
        if path:
            self._cookie_loaded_via = "auto"
            self.ck_path.setText(path.replace("\\", "/"))  # 触发 _on_cookie_change
            name = os.path.basename(path)
            self._log(
                f"✓ 已自动从下载目录加载 Cookie「{name}」，"
                f"无需再点「选择文件」（路径：{path}）",
                "ok",
            )
            self._set_status("Cookie 已自动加载 ✓", tk("ok"))
        else:
            self._cookie_loaded_via = None
            self._set_cookie_btn_loaded(False)
            self._log(
                "下载目录未找到 e-hentai Cookie"
                "（优先 e-hentai.org_cookies.txt，Netscape .txt），"
                "请点「选择文件」手动指定；公开图库也可不填",
                "warn",
            )

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
        has_eh = (
            "e-hentai.org" in low
            or "exhentai.org" in low
            or ".e-hentai." in low
            or ".exhentai." in low
        )
        lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
        count = len(lines)
        strong = any(
            k in text
            for k in (
                "ipb_member_id", "ipb_pass_hash", "ipb_session_id",
                "igneous", "sk",
            )
        )


        if not has_eh:
            msg = "⚠ 文件中未见 e-hentai / exhentai 域"
            self.ck_status.setFullText(msg)
            self.ck_status.setStyleSheet(f"color:{tk('warn')}; font-size:12px;")
            # 仍算已选文件，可能用户有意用其它站 Cookie
            self._set_cookie_btn_loaded(True)
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
            msg = f"⚠ {prefix}可能缺少登录字段（ipb_member_id / ipb_pass_hash 等）"
            self.ck_status.setStyleSheet(f"color:{tk('warn')}; font-size:12px;")
            self._set_cookie_btn_loaded(True)
        else:
            msg = "⚠ Cookie 文件为空"
            self.ck_status.setStyleSheet(f"color:{tk('warn')}; font-size:12px;")
            self._set_cookie_btn_loaded(False)
        self.ck_status.setFullText(msg)

    def _choose_dir(self):
        d = QFileDialog.getExistingDirectory(
            self, "选择保存目录",
            self.save_edit.text().strip() or os.path.expanduser("~"),
        )
        if d:
            self.save_edit.setText(d.replace("\\", "/"))

    def _show_help(self):
        from styles.style_all import message_box_info
        message_box_info(
            self,
            "图集下载说明",
            "1. 粘贴 e-hentai / exhentai 图库链接，自动解析图片直链并下载\n\n"
            "2. Cookie：用扩展导出 Netscape cookies.txt\n"
            "   推荐文件名 e-hentai.org_cookies.txt，放入下载目录自动加载\n"
            "   exhentai 必须登录后导出，e-hentai 公开图库可不填\n\n"
            "3. 内容卡亮起 = 待下载，变灰 = 已下载，点击灰卡可重新亮起重下\n\n"
            "4. 请控制下载间隔，避免触发站点限制",
        )

    def _find_page_video_engine(self):
        """查找主窗口上的 PageVideo（自动下载引擎）。"""
        w = self
        while w is not None:
            pv = getattr(w, "page_douyin", None)
            if pv is not None and hasattr(pv, "start_batch_from_file"):
                return pv, w
            try:
                w = w.parent()
            except Exception:
                break
        return None, None

    def _batch_choose_file(self):
        """图集「批处理」：选文件后统一走自动下载（支持混链）。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择批处理文件", os.path.expanduser("~"),
            "文本文件 (*.txt);;所有文件 (*)"
        )
        if not path:
            return
        pv, mw = self._find_page_video_engine()
        if pv is None:
            self._log("未找到自动下载引擎，无法启动批处理", "err")
            self._set_status("自动下载引擎不可用", tk("err"))
            return
        if mw is not None and hasattr(mw, "stack") and hasattr(mw, "btn_douyin"):
            try:
                idx = mw.stack.indexOf(pv)
                if idx >= 0:
                    mw._switch(idx, mw.btn_douyin)
            except Exception:
                pass
        self._log("批处理 → 自动下载（混链按平台分发）…", "ok")
        if not pv.start_batch_from_file(path):
            self._log("自动下载未能启动", "warn")
            self._set_status("自动下载未启动", tk("warn"))

    def request_batch_stop(self) -> bool:
        """F6：优先转自动下载引擎中止；其次序列补全；再处理本页遗留本地批。"""
        pv, _mw = self._find_page_video_engine()
        if pv is not None and getattr(pv, "_batch_mode", False):
            if hasattr(pv, "request_batch_stop"):
                return bool(pv.request_batch_stop())
            return False
        # 序列补全进行中：立刻置取消（解析/下载线程会响应）
        if getattr(self, "_fill_active_once", False) or getattr(
            self, "_fill_only_names", None
        ) is not None:
            try:
                self._cancel[0] = True
            except Exception:
                pass
            set_batch_progress_note("正在中止…")
            try:
                self._fill_bubble(note="正在中止…")
            except Exception:
                pass
            return True
        if not getattr(self, "_batch_mode", False):
            return False
        if getattr(self, "_batch_stop_requested", False):
            set_batch_progress_note("已预约中止")
            return True
        self._batch_stop_requested = True
        busy = False
        for attr in ("_dl_worker", "_parse_worker"):
            w = getattr(self, attr, None)
            try:
                if w is not None and w.isRunning():
                    busy = True
                    break
            except Exception:
                pass
        if not busy:
            try:
                self._batch_timer.stop()
            except Exception:
                pass
            QTimer.singleShot(0, self._batch_next)
        else:
            set_batch_progress_note("当前条后中止")
        return True

    def _batch_current_record_url(self) -> str:
        """当前条写入记录文件时的原文（优先输入框，其次本页批处理列表）。"""
        text = ""
        try:
            text = (self.url_edit.text() or "").strip()
        except Exception:
            text = ""
        if text:
            return text
        if getattr(self, "_batch_mode", False):
            urls = getattr(self, "_batch_urls", None) or []
            idx = int(getattr(self, "_batch_index", 0) or 0) - 1
            if 0 <= idx < len(urls):
                return (urls[idx] or "").strip()
        return ""

    def _batch_remove_current_record_line(self) -> bool:
        """本页批处理：校验成功后从记录文件删当前行。"""
        path = (getattr(self, "_batch_file_path", None) or "").strip()
        url = self._batch_current_record_url()
        if not path or not url:
            return False
        ok = _batch_remove_url_from_file(path, url)
        if ok:
            self._log(f"✓ 已从记录删除：{url[:60]}…", "ok")
        else:
            self._log(f"⚠ 记录删行未匹配：{url[:60]}…", "warn")
        return ok

    def _report_unified_batch_result(self, ok: bool = None, note: str = ""):
        """把本条结果上报给 PageVideo 自动下载引擎（校验通过才删行）。"""
        if ok is None:
            ok = bool(getattr(self, "_last_download_ok", False))
        note = (note or getattr(self, "_last_batch_note", None) or "").strip()
        if not note:
            note = "成功" if ok else "失败"
        eng = _find_unified_batch_engine(self)
        if eng is None:
            return False
        try:
            eng.notify_batch_item_done(
                bool(ok),
                note=note,
                source=getattr(self, "_BATCH_ENGINE_KEY", "") or "",
            )
            return True
        except Exception:
            return False

    def _batch_schedule_next(self, delay_ms: int = 2000):
        """预约下一条；若已 F6 中止则立刻收口。"""
        if getattr(self, "_batch_stop_requested", False):
            try:
                self._batch_timer.stop()
            except Exception:
                pass
            QTimer.singleShot(0, self._batch_next)
            return
        self._batch_timer.start(int(delay_ms))

    def _batch_next(self):
        if getattr(self, "_batch_stop_requested", False) or (
            not self._batch_mode or not self._batch_urls
            or self._batch_index >= len(self._batch_urls)
        ):
            cnt = self._batch_index
            ok = self._batch_ok
            err = self._batch_err
            skip = self._batch_skip
            path = self._batch_file_path
            stopped = bool(getattr(self, "_batch_stop_requested", False))
            self._batch_urls = []
            self._batch_index = 0
            self._batch_mode = False
            self._batch_file_path = ""
            self._batch_ok = 0
            self._batch_err = 0
            self._batch_skip = 0
            self._batch_stop_requested = False
            if hasattr(self, "btn_batch"):
                self.btn_batch.setEnabled(True)
            try:
                stop_batch_progress_toast()
            except Exception:
                pass
            # 批处理收口：强制隐藏简易进度线，避免异常路径漏 hide
            try:
                self._emit_nav_progress(False)
            except Exception:
                pass
            # 完成摘要不再写本页状态/日志（统一由速存图文侧报告承接）
            if cnt > 0:
                show_cursor_toast(
                    "批处理",
                    "已中止" if stopped else "完成",
                    current=cnt, total=cnt,
                    ok=ok, skip=skip, err=err,
                    accent="info" if stopped else "ok",
                )
            if cnt > 0 and not stopped and err == 0 and path and os.path.isfile(path):
                # 逐条已删行；文件空了则删文件
                try:
                    with open(path, "r", encoding="utf-8-sig") as f:
                        remaining = [
                            ln for ln in f
                            if ln.strip() and not ln.strip().startswith("#")
                        ]
                    if not remaining:
                        os.remove(path)
                except Exception:
                    pass
            return
        url = self._batch_urls[self._batch_index].strip()
        self._batch_index += 1
        pct = int(self._batch_index / len(self._batch_urls) * 100)
        self._set_status(f"{url[:50]}…", self._progress_gradient_color(pct))
        show_batch_progress_toast(
            getattr(self, "_batch_platform_label", None) or self._BATCH_LABEL,
            self._batch_index,
            len(self._batch_urls),
        )
        self.url_edit.setText(url)
        self._start_flow(False)

    # ── 主流程 ───────────────────────────────────────────────────────────────

    def _clipboard_text(self) -> str:
        cb = QApplication.clipboard()
        return (cb.text() or "").strip() if cb else ""

    def _start_flow(self, use_clipboard=True):
        if getattr(self, "_is_placeholder", False):
            return
        text = ""
        if use_clipboard:
            raw = self._clipboard_text()
            try:
                if raw:
                    extract_url_from_text(raw)
                    text = raw
                    self.url_edit.setText(text)
                    self._log("已从剪贴板读取链接", "ok")
            except Exception:
                text = ""
        if not text:
            text = self.url_edit.text().strip()
        if not text:
            self._log("剪贴板与输入框都没有链接", "warn")
            self._set_status("没有链接", tk("warn"))
            return
        self._auto_dl = True
        self._parse(text)

    def _parse(self, url_text: str):
        if self._parse_worker and self._parse_worker.isRunning():
            self._log("正在解析中…", "warn")
            return
        if self._dl_worker and self._dl_worker.isRunning():
            self._log("正在下载中，请先等待或取消", "warn")
            return

        self._cancel[0] = False
        self._last_download_ok = False
        self._skip_parse_err = False
        self.btn_sel_dl.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self._log("开始解析…")
        self._clear_name_cards()
        self._show_empty_cards()
        self._info = None
        self._last_failed = []
        self._set_status("解析中…", tk("warn"))
        # 侧栏：解析一开始就出灰色整线
        self._emit_nav_progress(True, mode="busy")

        # 序列补全：只解析真实缺失页码（不扫整本）
        only_indices = None
        fill_names = getattr(self, "_fill_only_names", None)
        if fill_names:
            only_indices = sorted(_eh_normalize_only_indices(fill_names))
            if only_indices:
                self._log(
                    f"补全定点解析：{len(only_indices)} 页"
                    f"（例 {only_indices[0]:04d}"
                    f"{'…' + f'{only_indices[-1]:04d}' if len(only_indices) > 1 else ''}）",
                    "ok",
                )
                self._fill_bubble(note="解析中", current=0, total=len(only_indices))
            else:
                only_indices = None

        self._parse_worker = GalleryParseWorker(
            url_text,
            self.ck_path.text().strip(),
            self._cancel,
            only_indices=only_indices,
        )
        self._parse_worker.ok.connect(self._on_parse_ok)
        self._parse_worker.err.connect(self._on_parse_err)
        self._parse_worker.log.connect(self._log)
        self._parse_worker.finished.connect(self._on_parse_finished)
        self._parse_worker.title_ready.connect(self._on_title_ready)
        self._parse_worker.start()

    def _batch_toast_already(self) -> bool:
        """批处理中：把「已下载过」写到常驻气泡第一行末尾。返回是否处于批处理。"""
        # 本页批处理
        if getattr(self, "_batch_mode", False) and getattr(self, "_batch_urls", None):
            total = len(self._batch_urls or [])
            if total > 0:
                show_batch_progress_toast(
                    getattr(self, "_batch_platform_label", None)
                    or getattr(self, "_BATCH_LABEL", "") or "",
                    int(getattr(self, "_batch_index", 0) or 0),
                    total,
                    note="已下载过",
                )
                return True
        # 自动下载：图集不在 PageVideo 父链上，直接改常驻气泡状态词
        if set_batch_progress_note("已下载过"):
            return True
        return False

    def _mark_already_downloaded(self, label: str) -> None:
        """命中防重复记录：日志/气泡 + 批处理跳过，自动下载记为成功以免当失败。"""
        self._log("━" * 36, "info")
        self._log(f"📋 已下载记录：{label}", "info")
        self._log("该图集已在下载记录中，跳过重复下载", "ok")
        self._log("━" * 36, "info")
        try:
            self.lbl_hint.setText(f"📋 已下过 · {str(label)[:40]}")
            self.lbl_hint.setStyleSheet(
                "color:%s; font-weight:600; font-size:12px;" % tk("ok")
            )
        except Exception:
            pass
        # 批处理：常驻气泡第一行追加「已下载过」；非批处理仍弹独立小提示
        if not self._batch_toast_already():
            show_cursor_toast("下载", "已下载过", accent="ok")
        # 自动下载（PageVideo）看 _last_download_ok / _last_batch_note
        self._last_download_ok = True
        self._last_batch_note = "已下载过"
        self._auto_dl = False
        if self._batch_mode:
            # 批处理：重复也算本条完成，删记录行后跳下一条
            self._batch_skip += 1
            self._batch_ok += 1
            self._batch_remove_current_record_line()
            self._batch_schedule_next(1000)
        else:
            self._emit_nav_progress(False)
            # 自动下载：显式上报，引擎校验成功后删记录行
            self._report_unified_batch_result(True, note="已下载过")

    def _on_title_ready(self, title: str):
        folder_title = _safe_folder_name(title, "gallery")
        if _should_skip_by_record(self, folder_title, note_continue=False):
            self._cancel[0] = True
            self._skip_parse_err = True
            self._mark_already_downloaded(folder_title)

    def _on_parse_finished(self):
        if not (self._dl_worker and self._dl_worker.isRunning()):
            self.btn_cancel.setEnabled(False)
            self._update_action_buttons()
        # 侧栏线不在这里关：成功由 _on_parse_ok（非自动下才关）/ 失败由 _on_parse_err；
        # 自动下载会 singleShot 启动，中间需保持灰线，避免闪灭

    def _work_title(self, fallback: str = "gallery") -> str:
        """当前作品目录用的标题（含「改名」后的覆盖名）。"""
        ov = getattr(self, "_folder_title_override", None)
        if ov:
            return str(ov)
        return (self._info or {}).get("title") or fallback

    def _gallery_dir(self) -> str:
        ov = (getattr(self, "_gallery_dir_override", None) or "").strip()
        if ov:
            return ov
        save_dir = (self.save_edit.text() or "").strip()
        if not save_dir:
            save_dir = os.path.expanduser("~/Downloads/Galleries")
        return gallery_folder_path(save_dir, self._work_title("gallery"))

    def _clear_fill_state(self):
        self._fill_only_names = None
        self._fill_report_path = ""
        self._gallery_dir_override = ""
        self._bypass_record = False
        self._fill_pending_url = ""
        self._fill_planned_n = 0
        self._fill_gallery_folder_snap = ""
        self._fill_actual_folder = ""
        self._fill_current_i = 0
        self._fill_ok_n = 0
        self._fill_err_n = 0
        self._fill_skip_n = 0

    def _fill_record_name(self) -> str:
        """气泡上行名：补全目标文件夹名（补全会话必有目录）。"""
        folder = (
            (getattr(self, "_fill_gallery_folder_snap", None) or "").strip()
            or (getattr(self, "_gallery_dir_override", None) or "").strip()
        )
        return os.path.basename(folder.rstrip("\\/"))

    def _fill_bubble(self, note: str = "", *, current=None, total=None):
        """序列补全：批处理同款常驻气泡（跟随鼠标两行）。"""
        if not getattr(self, "_fill_active_once", False) and getattr(
            self, "_fill_only_names", None
        ) is None:
            return
        try:
            y = int(total if total is not None else (getattr(self, "_fill_planned_n", 0) or 0))
        except (TypeError, ValueError):
            y = 0
        if y <= 0:
            return
        try:
            x = int(
                current
                if current is not None
                else (getattr(self, "_fill_current_i", 0) or 0)
            )
        except (TypeError, ValueError):
            x = 0
        x = max(0, min(x, y))
        show_batch_progress_toast(
            "",
            x,
            y,
            note=note or "",
            ok=int(getattr(self, "_fill_ok_n", 0) or 0),
            err=int(getattr(self, "_fill_err_n", 0) or 0),
            skip=int(getattr(self, "_fill_skip_n", 0) or 0),
            record_name=self._fill_record_name(),
        )

    def _finish_sequence_fill(self, *, ok: bool, summary: str, detail: str = ""):
        """序列补全收口：结果提示 + 切回「速存图文」+ 刷新序列检查。

        内部会快照目录/计划数，再同步清单（已齐删除 / 未齐重写）并清理补全状态。
        """
        if getattr(self, "_fill_finishing", False):
            return
        # 仅补全会话可进（handoff 时置 _fill_active_once）
        if not getattr(self, "_fill_active_once", False) and getattr(
            self, "_fill_only_names", None
        ) is None:
            return
        self._fill_finishing = True
        # 气泡先写最终状态，再关掉（与批处理收口一致）
        try:
            note = "成功" if ok else "失败"
            if "未齐" in (summary or "") or "仍缺" in (summary or ""):
                note = "未齐"
            self._fill_bubble(note=note, current=getattr(self, "_fill_planned_n", 0) or 0)
        except Exception:
            pass
        try:
            stop_batch_progress_toast()
        except Exception:
            pass
        self._fill_active_once = False
        try:
            folder = (
                (getattr(self, "_gallery_dir_override", None) or "").strip()
                or (getattr(self, "_fill_gallery_folder_snap", None) or "").strip()
            )
            planned = int(getattr(self, "_fill_planned_n", 0) or 0)
            names = getattr(self, "_fill_only_names", None) or set()
            if not planned:
                planned = len(names)

            still_n = 0
            if names and folder:
                for miss in names:
                    stem = os.path.splitext(str(miss).strip())[0]
                    if not image_file_exists(folder, stem) and not image_file_exists(
                        folder, str(miss).strip()
                    ):
                        still_n += 1
            elif names and not folder:
                still_n = len(names)

            try:
                self._maybe_delete_fill_report()
            except Exception:
                pass
            # 以盘面为准：计划项都在盘上即成功（避免解析/统计口径不一致误报失败）
            if names and folder and still_n == 0:
                ok = True
            self._clear_fill_state()

            head = (summary or "").strip() or ("补全成功" if ok else "补全失败")
            if still_n > 0:
                ok = False
                if "仍缺" not in head:
                    head = f"补全未齐 · 仍缺 {still_n}" + (
                        f"/{planned}" if planned else ""
                    )
            elif ok:
                if planned and "成功" not in head and "完成" not in head:
                    head = f"补全成功 · {planned} 项"
                elif "失败" in head:
                    head = f"补全成功 · {planned} 项" if planned else "补全成功"

            lines = [head]
            if folder:
                lines.append(f"目录：{folder}")
            if (detail or "").strip():
                lines.append(detail.strip())
            if still_n > 0:
                lines.append("可再次勾选「补全」并指定文件夹重试")
            body = "\n".join(lines)

            # 切回速存图文 + 刷新序列区（进度气泡已关，结果用弹窗）
            mw = None
            try:
                w = self
                while w is not None:
                    if getattr(w, "page_fast", None) is not None and hasattr(
                        w, "_switch"
                    ):
                        mw = w
                        break
                    try:
                        w = w.parent()
                    except Exception:
                        break
            except Exception:
                mw = None
            if mw is not None:
                try:
                    pf = mw.page_fast
                    if hasattr(mw, "btn_fast") and hasattr(mw, "stack"):
                        mw._switch(mw.stack.indexOf(pf), mw.btn_fast)
                    if hasattr(pf, "on_sequence_fill_done"):
                        pf.on_sequence_fill_done(
                            ok=bool(ok),
                            summary=head,
                            folder=folder,
                            detail=detail or "",
                            still_n=still_n,
                            planned_n=planned,
                        )
                except Exception:
                    pass

            try:
                self._show_fill_result_dialog(ok=bool(ok), body=body)
            except Exception:
                pass
        finally:
            self._fill_finishing = False
            try:
                stop_batch_progress_toast()
            except Exception:
                pass

    def _show_fill_result_dialog(self, *, ok: bool, body: str):
        """序列补全结果弹窗：3 秒倒计时自动关闭。"""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QDialogButtonBox

        parent = self.window() or self
        dlg = QDialog(parent)
        dlg.setWindowTitle("序列补全")
        dlg.setModal(True)
        dlg.setMinimumWidth(420)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(10)

        head = QLabel("补全成功" if ok else "补全未完成")
        head.setStyleSheet(
            "font-size:15px; font-weight:700; background:transparent; color:%s;"
            % ("#22c55e" if ok else "#ef4444")
        )
        lay.addWidget(head)

        msg = QLabel((body or "").replace("\n", "<br>"))
        msg.setWordWrap(True)
        msg.setTextFormat(Qt.RichText)
        msg.setStyleSheet("background:transparent; font-size:13px;")
        lay.addWidget(msg)

        countdown_sec = 3
        lbl_cd = QLabel(f"{countdown_sec} 秒后自动关闭…")
        lbl_cd.setStyleSheet("background:transparent; color:#94a3b8; font-size:12px;")
        lay.addWidget(lbl_cd)

        btn_box = QDialogButtonBox()
        btn_ok = btn_box.addButton("知道了", QDialogButtonBox.AcceptRole)
        btn_ok.setDefault(True)
        lay.addWidget(btn_box)

        state = {"left": countdown_sec, "done": False}

        def _tick():
            if state["done"]:
                return
            state["left"] -= 1
            if state["left"] <= 0:
                state["done"] = True
                timer.stop()
                dlg.accept()
                return
            lbl_cd.setText(f"{state['left']} 秒后自动关闭…")
            btn_ok.setText(f"知道了（{state['left']}）")

        timer = QTimer(dlg)
        timer.setInterval(1000)
        timer.timeout.connect(_tick)
        btn_ok.setText(f"知道了（{countdown_sec}）")

        def _accept():
            state["done"] = True
            timer.stop()
            dlg.accept()

        btn_box.accepted.connect(_accept)
        timer.start()
        dlg.exec_()
        timer.stop()

    def handoff_fill_missing(
        self,
        source_url: str,
        gallery_folder: str,
        missing_names,
        *,
        report_path: str = "",
    ) -> bool:
        """序列文件检查「补全」入口（仅本页 e-hentai.org）。

        流程（独立于剪贴板自动下载）：
          1) 规范化图库 URL（必须是 e-hentai.org/g/… 页，不是 hath 直链）
          2) 写入 url 框、锁定目标目录、绕过「已下载记录」
          3) 下一拍再解析（等切页/UI 就绪），解析成功后只下序列缺失项
        """
        # 规范化：杜绝「有 e-hentai 字样但无 http」或整段清单正文直接进解析
        url = extract_eh_gallery_url(source_url or "")
        if not url:
            try:
                url = extract_url_from_text(source_url or "")
            except Exception:
                url = ""
        if not url or (
            "e-hentai.org" not in url.lower() and "exhentai.org" not in url.lower()
        ):
            self._log(
                "补全失败：未识别到 e-hentai 图库链接"
                f"（原文前 120 字：{(source_url or '')[:120]!r}）",
                "err",
            )
            return False
        # 再挡一层：误把 hath 直链当图库
        if "hath.network" in url.lower() or "/h/" in url.lower() and "/g/" not in url.lower():
            self._log("补全失败：需要图库页链接（…/g/ID/token/），不是图片直链", "err")
            return False

        folder = os.path.abspath((gallery_folder or "").strip())
        if not folder or not os.path.isdir(folder):
            self._log(f"补全失败：目录无效 {folder}", "err")
            return False
        names = {str(x).strip() for x in (missing_names or []) if str(x).strip()}
        if not names:
            self._log("补全：序列检查无缺失项，已跳过", "ok")
            return False
        if self._parse_worker and self._parse_worker.isRunning():
            self._log("补全失败：正在解析中", "warn")
            return False
        if self._dl_worker and self._dl_worker.isRunning():
            self._log("补全失败：正在下载中", "warn")
            return False

        self._fill_only_names = names
        self._fill_planned_n = len(names)
        self._fill_gallery_folder_snap = folder
        self._fill_report_path = (report_path or "").strip() or os.path.join(
            folder, MISSING_REPORT_NAME
        )
        self._gallery_dir_override = folder
        self._bypass_record = True
        self._fill_active_once = True
        self._fill_current_i = 0
        self._fill_ok_n = 0
        self._fill_err_n = 0
        self._fill_skip_n = 0
        parent = os.path.dirname(folder)
        leaf = os.path.basename(folder.rstrip("\\/")) or "gallery"
        if parent:
            try:
                self.save_edit.setText(parent.replace("\\", "/"))
            except Exception:
                pass
        self._folder_title_override = leaf
        try:
            self.url_edit.setText(url)
        except Exception:
            pass
        self._auto_dl = True
        self._fill_pending_url = url
        # 批处理同款常驻气泡
        self._fill_bubble(note="准备中", current=0)
        self._log("━" * 36, "info")
        self._log(
            f"序列补全(e-hentai) · 待补 {len(names)} 项",
            "ok",
        )
        self._log(f"目标目录：{folder}", "info")
        self._log(f"图库链接：{url}", "ok")
        self._log("下一步：解析图库 → 只下载序列缺失序号", "info")
        self._log("━" * 36, "info")
        # 切页后再解析，避免与 showEvent/Cookie 自动加载抢同一拍
        QTimer.singleShot(0, self._run_fill_parse)
        return True

    def _run_fill_parse(self):
        """补全专用解析：只用 handoff 里锁定的图库 URL，不读剪贴板。"""
        url = (getattr(self, "_fill_pending_url", None) or "").strip()
        if not url:
            url = extract_eh_gallery_url(self.url_edit.text() if hasattr(self, "url_edit") else "")
        if not url:
            self._log("补全解析中止：图库链接为空", "err")
            self._emit_nav_progress(False)
            self._finish_sequence_fill(
                ok=False,
                summary="补全失败 · 图库链接为空",
                detail="解析前丢失链接，请重新指定文件夹。",
            )
            return
        if not getattr(self, "_fill_only_names", None):
            self._log("补全解析中止：无缺失列表", "warn")
            self._emit_nav_progress(False)
            self._finish_sequence_fill(
                ok=False,
                summary="补全失败 · 无缺失列表",
            )
            return
        self._auto_dl = True
        self._fill_bubble(note="解析中", current=0)
        self._parse(url)

    def _apply_fill_card_selection(self) -> int:
        """只亮起「序列检查」报告的真实缺失名（与清单内缺失列表无关）。"""
        names = getattr(self, "_fill_only_names", None)
        if not names:
            return 0
        folder = self._gallery_dir()
        sel_n = 0
        matched = set()
        for card in getattr(self, "_name_cards", []) or []:
            cname = getattr(card, "_name", "") or ""
            if fill_name_matches(cname, names) and not image_file_exists(folder, cname):
                card.set_downloaded(False)
                card.set_selected(True)
                sel_n += 1
                matched.add(cname)
            else:
                # 非补全目标：已在盘则灰，否则也不选（避免误下清单外项）
                if image_file_exists(folder, cname):
                    card.set_downloaded(True, keep_selected=False)
                else:
                    card.set_downloaded(False)
                    card.set_selected(False)
        # 序列有号、解析却无对应卡
        unmatched = []
        for miss in names:
            hit = any(fill_name_matches(c, {miss}) for c in matched)
            if not hit:
                # 也可能 matched 用的是 card name，再扫全部卡
                any_card = any(
                    fill_name_matches(getattr(c, "_name", ""), {miss})
                    for c in (self._name_cards or [])
                )
                if not any_card:
                    unmatched.append(miss)
        if unmatched:
            preview = "、".join(unmatched[:8])
            more = f" 等{len(unmatched)}个" if len(unmatched) > 8 else ""
            self._log(
                f"补全：解析结果中无对应图源卡（序列名）：{preview}{more}",
                "warn",
            )
        self._update_action_buttons()
        return sel_n

    def _maybe_delete_fill_report(self):
        """补全收口：按盘面同步 _缺失文件清单.txt。

        · 计划项都已落盘 → 删除清单
        · 仍有缺失 / 本次失败 → 按仍缺名单重写清单（保留作品/链接/应下）
        """
        report = (getattr(self, "_fill_report_path", None) or "").strip()
        names = getattr(self, "_fill_only_names", None)
        folder = (
            (getattr(self, "_fill_actual_folder", None) or "").strip()
            or (getattr(self, "_gallery_dir_override", None) or "").strip()
            or (getattr(self, "_fill_gallery_folder_snap", None) or "").strip()
            or self._gallery_dir()
        )
        if not report and not names:
            return
        rp = report or (
            os.path.join(folder, MISSING_REPORT_NAME) if folder else ""
        )
        info = getattr(self, "_info", None) or {}
        meta = parse_missing_report(rp) if rp and os.path.isfile(rp) else {}
        expected = int(meta.get("expected_count") or 0) or len(
            info.get("images") or []
        )
        try:
            still_rows = collect_fill_still_rows(
                folder,
                names,
                report_path=rp,
                info=info,
                last_failed=getattr(self, "_last_failed", None),
                expected_count=expected,
            )
        except Exception as e:
            self._log(f"统计仍缺项失败：{e}", "warn")
            still_rows = []
        title = meta.get("title") or info.get("title") or ""
        source = meta.get("source_url") or info.get("source_url") or ""
        existed = bool(rp and os.path.isfile(rp))
        ok_n = int(getattr(self, "_fill_ok_n", 0) or 0) + int(
            getattr(self, "_fill_skip_n", 0) or 0
        )
        fail_n = int(getattr(self, "_fill_err_n", 0) or 0) or len(
            getattr(self, "_last_failed", None) or []
        )
        # 下载报了失败但盘面误判已齐：仍按失败项写回，避免把清单删光
        if not still_rows and fail_n > 0:
            for it in getattr(self, "_last_failed", None) or []:
                nm = str((it or {}).get("name") or "").strip()
                url = str((it or {}).get("url") or "").strip()
                if nm:
                    still_rows.append((nm, url))
        try:
            dest = sync_missing_report(
                folder,
                still_rows,
                report_path=rp,
                title=title,
                source_url=source,
                expected_count=expected,
                ok_n=ok_n,
                fail_n=fail_n,
            )
        except Exception as e:
            self._log(f"同步缺失清单失败：{e}", "warn")
            return
        if not still_rows:
            if existed:
                self._log(f"已删除 {os.path.basename(rp or MISSING_REPORT_NAME)}", "ok")
            return
        self._log(
            f"补全未齐，已更新清单（仍缺 {len(still_rows)} 项）",
            "warn",
        )
        if dest:
            self._log(f"📄 缺失清单已更新：{os.path.basename(dest)}", "warn")

    def _save_root(self) -> str:
        save_dir = (self.save_edit.text() or "").strip()
        if not save_dir:
            save_dir = os.path.expanduser("~/Downloads/Galleries")
        return save_dir

    def _resolve_same_name(
        self,
        images: list,
        *,
        dialog_title: str = "图集下载 · 发现同名文件",
        fallback_leaf: str = "gallery",
    ):
        """同名处理（两种情况）：

        1) 有同名但未下全 → 不弹窗，继续下载缺失项（skip_existing=True）
        2) 有同名且内容卡已全部在盘 → 弹窗：中止 / 改名 / 覆盖

        返回:
          None  — 中止（调用方应停止）
          dict  — {title, skip_existing}
                  skip_existing=None：无同名、沿用调用方
                  True：跳过已有（续下或改名新目录）
                  False：覆盖
        """
        images = list(images or [])
        save_dir = self._save_root()
        title = self._work_title(fallback_leaf)
        folder = gallery_folder_path(save_dir, title)

        total = len(images)
        if total <= 0:
            return {"title": title, "skip_existing": None}

        # 按「内容卡」统计磁盘上已有几张
        done_n = 0
        for it in images:
            name = str(it.get("name") or f"{int(it.get('index') or 0):04d}")
            if image_file_exists(folder, name):
                done_n += 1

        if done_n <= 0:
            # 目录里没有同名序号：正常下
            return {"title": title, "skip_existing": None}

        if done_n < total:
            # 情况1：有同名但没下全 → 静默续下
            self._log(
                f"目录已有 {done_n}/{total} 张，未下全 — 继续下载缺失项（不弹窗）",
                "ok",
            )
            return {"title": title, "skip_existing": True}

        # 情况2：内容卡已全部在盘 → 才弹窗
        try:
            from utils.download_confirm import (
                confirm_overwrite_or_abort,
                existing_files,
                next_numbered_dir_title,
                ACTION_ABORT,
                ACTION_OVERWRITE,
                ACTION_RENAME,
            )
        except Exception as e:
            self._log(f"同名确认模块加载失败：{e}", "warn")
            return {"title": title, "skip_existing": None}

        conflicts = existing_files(
            self._collect_gallery_conflicts(images, save_dir, title_override=title)
        )
        if not conflicts:
            # 理论不应发生（done_n==total）
            return {"title": title, "skip_existing": None}

        self._log(
            f"目录已齐 {done_n}/{total} 张（同名序号，内容未必相同）— 请选择处理方式",
            "warn",
        )
        action = confirm_overwrite_or_abort(
            self,
            conflicts,
            title=dialog_title,
            rename_hint="作品目录名后加序号（另建文件夹，不混入旧图）",
        )
        if action is ACTION_ABORT:
            self._log("已中止（目录已下全，用户选择中止）", "warn")
            self._set_status("已中止", tk("warn"))
            return None
        if action is ACTION_RENAME:
            old_leaf = _safe_folder_name(title, fallback_leaf)
            new_leaf = next_numbered_dir_title(save_dir, old_leaf)
            self._folder_title_override = new_leaf
            self._log(
                f"目录已下全：改用新目录「{new_leaf}」（原「{old_leaf}」保留）",
                "ok",
            )
            return {"title": new_leaf, "skip_existing": True}
        self._log("目录已下全：将覆盖已有同名文件", "warn")
        return {"title": title, "skip_existing": False}

    def _mark_all_cards_for_redownload(self):
        """覆盖模式：全部亮起，当作未下载。"""
        for card in getattr(self, "_name_cards", []) or []:
            try:
                card.set_downloaded(False)
                card.set_selected(True)
            except Exception:
                pass
        self._update_action_buttons()

    def _open_save_dir(self):
        """打开图集保存目录（有解析结果则进作品子目录，否则进根保存路径）。"""
        path = ""
        try:
            if getattr(self, "_info", None):
                path = (self._gallery_dir() or "").strip()
        except Exception:
            path = ""
        if not path:
            try:
                path = (self.save_edit.text() or "").strip()
            except Exception:
                path = ""
        if not path:
            path = os.path.expanduser("~/Downloads/Galleries")
        try:
            if not os.path.isdir(path):
                # 作品子目录尚未创建时退回保存根
                root = (self.save_edit.text() or "").strip() or os.path.expanduser("~/Downloads")
                if os.path.isdir(root):
                    path = root
                else:
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

    def _refresh_record_count(self, added_names=None):
        _gallery_apply_record_hint(self, added_names)

    def _on_view_file(self):
        _open_gallery_records_dialog(self)

    def _on_update_list(self):
        start = (self.save_edit.text() or "").strip()
        if not start:
            start = os.path.expanduser("~/Downloads/Galleries")
        folder = QFileDialog.getExistingDirectory(self, "选择要扫描的文件夹", start)
        if not folder:
            return
        added = scan_dir_records("gallery_eh", folder)
        names = sorted(added) if added else []
        if names:
            self._log(f"已下载：新增 {len(names)} 条记录", "ok")
        else:
            self._log("已下载：未发现新记录", "ok")
        self._refresh_record_count(names)

    def _missing_list(self) -> list:
        if not self._info:
            return []
        return list_missing_images(self._info.get("images") or [], self._gallery_dir())

    def _selected_images(self) -> list:
        """当前亮起（选中）的内容卡对应条目。"""
        out = []
        for card in getattr(self, "_name_cards", []) or []:
            if not card.is_selected():
                continue
            it = dict(card.item or {})
            it["name"] = card._name
            out.append(it)
        return out

    def _update_action_buttons(self):
        has = bool(self._info and self._info.get("images"))
        busy = bool(
            (self._dl_worker and self._dl_worker.isRunning())
            or (self._parse_worker and self._parse_worker.isRunning())
        )
        n_sel = len(self._selected_images()) if has else 0
        self.btn_sel_dl.setEnabled(has and not busy and n_sel > 0)

    def _on_parse_ok(self, info: dict):
        self._info = info
        self._last_failed = []
        # 序列补全保留目录覆盖；普通流程清掉改名覆盖
        if not getattr(self, "_fill_only_names", None):
            self._folder_title_override = None

        # 拿到文件夹名立即比对下载记录（title_ready 未拦住时的二次保险）
        folder_title = _safe_folder_name(self._work_title("gallery"), "gallery")
        if _should_skip_by_record(self, folder_title):
            self._mark_already_downloaded(folder_title)
            return

        imgs = info.get("images") or []
        title = info.get("title") or ""
        site = info.get("site") or ""
        self._fill_name_cards(imgs)
        self._update_action_buttons()
        self._set_status(f"√ {site} · {title[:40]}{'…' if len(title) > 40 else ''} · 共 {len(imgs)} 张", tk("ok"))

        # 序列补全：只选序列扫描缺失项，跳过同名弹窗与整本记录逻辑
        if getattr(self, "_fill_only_names", None):
            sel_n = self._apply_fill_card_selection()
            self._conflict_decision = {
                "title": self._work_title("gallery"),
                "skip_existing": True,
            }
            self._log(f"序列补全：将下载 {sel_n} 张真实缺失", "ok")
            if self._auto_dl:
                self._auto_dl = False
                if sel_n <= 0:
                    self._log("序列补全：无可下载项（可能已齐或未匹配到图源）", "warn")
                    self._last_download_ok = True
                    self._emit_nav_progress(False)
                    self._fill_bubble(note="已齐", current=self._fill_planned_n)
                    # 视为「无需再下 / 已齐」收口并切回速存
                    QTimer.singleShot(
                        0,
                        lambda: self._finish_sequence_fill(
                            ok=True,
                            summary="补全完成 · 无需下载",
                            detail="目标缺失在解析后已不存在（可能已齐或未匹配到图源卡）。",
                        ),
                    )
                    return
                self._fill_bubble(note="下载中", current=0, total=sel_n)
                # 下载阶段 total 以实际选中数为准
                self._fill_planned_n = sel_n
                QTimer.singleShot(0, self._download_selected)
            else:
                self._emit_nav_progress(False)
            return

        # 解析后：未下全静默续下；已下全才弹窗
        self._conflict_decision = None
        resolved = self._resolve_same_name(
            imgs,
            dialog_title="图集下载 · 发现同名文件",
            fallback_leaf="gallery",
        )
        if resolved is None:
            # 中止：取消自动下，保留当前磁盘显示；失败标记给自动下载引擎
            self._auto_dl = False
            self._last_download_ok = False
            self._last_batch_note = "失败"
            self._emit_nav_progress(False)
            if not self._batch_mode:
                self._report_unified_batch_result(False, note="失败")
            return
        if resolved.get("skip_existing") is not None:
            # 用户已决策，下载阶段勿再弹
            self._conflict_decision = dict(resolved)
        if resolved.get("skip_existing") is True and getattr(
            self, "_folder_title_override", None
        ):
            self._fill_name_cards(imgs)
            self._update_action_buttons()
        elif resolved.get("skip_existing") is False:
            self._mark_all_cards_for_redownload()

        if self._auto_dl:
            self._auto_dl = False
            self._log("自动开始下载亮起的图源…", "ok")
            # 灰线保持到下载开始，由 _download_selected 切彩色
            QTimer.singleShot(0, self._download_selected)
        else:
            self._emit_nav_progress(False)

    def _on_parse_err(self, msg: str):
        if getattr(self, "_skip_parse_err", False):
            self._skip_parse_err = False
            return
        self._auto_dl = False
        self._last_download_ok = False
        self._last_batch_note = "失败"
        self._last_failed = []
        self.btn_sel_dl.setEnabled(False)
        self._set_status("解析失败 ✗", tk("err"))
        self._log("━" * 36, "err")
        for line in (msg or "").split("\n"):
            if line.strip():
                self._log(f"  {line.strip()}", "err")
        self._emit_nav_progress(False)
        if getattr(self, "_fill_only_names", None) is not None or getattr(
            self, "_fill_active_once", False
        ):
            brief = (msg or "").strip().split("\n")[0][:120]
            QTimer.singleShot(
                0,
                lambda b=brief: self._finish_sequence_fill(
                    ok=False,
                    summary="补全失败 · 解析失败",
                    detail=b,
                ),
            )
        if not self._batch_mode:
            self._report_unified_batch_result(False, note="失败")
        self._log("━" * 36, "err")

    def _toggle_hide_done(self):
        hide = self.chk_hide_done.isChecked()
        for c in (self._name_cards or []):
            if getattr(c, "_downloaded", False):
                c.setVisible(not hide)
        self._relayout_name_cards()

    def _sync_card_selection_ui(self):
        """灰卡点选后刷新「已下/选中」统计与「选中下载」可用性。"""
        cards = getattr(self, "_name_cards", None) or []
        if not cards:
            self._set_status("")
            self._update_action_buttons()
            return
        done_n = sum(1 for c in cards if getattr(c, "_downloaded", False))
        sel_n = sum(1 for c in cards if c.is_selected())
        self._set_status(
            f"已下 {done_n}/{len(cards)} · 选中 {sel_n}",
            self._done_ratio_color(done_n, len(cards)),
        )
        self._update_action_buttons()

    def _download_selected(self):
        """下载当前亮起的内容卡；已下载被点亮的会强制重下。"""
        if not self._info or not (self._info.get("images") or []):
            self._log("请先成功解析图集", "warn")
            self._last_download_ok = False
            self._emit_nav_progress(False)
            return
        jobs = self._selected_images()
        if not jobs:
            # 补全模式：无选中 → 直接按盘面收口，勿再弹同名窗
            if getattr(self, "_fill_only_names", None) is not None:
                self._log("补全：无待下卡片，按盘面结算", "warn")
                self._emit_nav_progress(False)
                QTimer.singleShot(
                    0,
                    lambda: self._finish_sequence_fill(
                        ok=True,
                        summary="补全完成 · 无需下载",
                        detail="没有亮起的图源卡（可能已齐）。",
                    ),
                )
                return
            # 全灰：可能是同名误判为已下 → 再弹一次同名确认
            all_imgs = list((self._info or {}).get("images") or [])
            resolved = self._resolve_same_name(
                all_imgs,
                dialog_title="图集下载 · 发现同名文件",
                fallback_leaf="gallery",
            )
            if resolved is None:
                self._last_download_ok = False
                self._emit_nav_progress(False)
                return
            if resolved.get("skip_existing") is None:
                self._log("没有亮起的内容卡 — 全部已下载，跳过", "ok")
                self._last_download_ok = True
                self._last_batch_note = "成功"
                self._emit_nav_progress(False)
                if self._batch_mode:
                    self._batch_ok += 1
                    self._batch_remove_current_record_line()
                    self._batch_schedule_next(2000)
                else:
                    self._report_unified_batch_result(True, note="成功")
                return
            if resolved.get("skip_existing") is True:
                self._fill_name_cards(all_imgs)
            else:
                self._mark_all_cards_for_redownload()
            jobs = self._selected_images() or all_imgs
            if not jobs:
                self._log("没有可下载的项目", "warn")
                self._last_download_ok = False
                self._emit_nav_progress(False)
                if getattr(self, "_fill_only_names", None) is not None:
                    QTimer.singleShot(
                        0,
                        lambda: self._finish_sequence_fill(
                            ok=False,
                            summary="补全失败 · 没有可下载项",
                        ),
                    )
                elif not self._batch_mode:
                    self._report_unified_batch_result(False, note="失败")
                return
            self._log(f"选中下载：{len(jobs)} 张（同名处理后）", "info")
            self._start_download(
                images=jobs,
                skip_existing=bool(resolved.get("skip_existing")),
                mode="download",
                retries=3,
            )
            return
        # 点亮的灰卡=要重下 → 整批不跳过并覆盖同名；纯新下则磁盘已有可跳过
        any_redown = any(
            getattr(c, "_downloaded", False) and c.is_selected()
            for c in (self._name_cards or [])
        )
        names = ", ".join((it.get("name") or "?") for it in jobs[:16])
        more = f" …共 {len(jobs)} 张" if len(jobs) > 16 else ""
        self._log(f"选中下载：{len(jobs)} 张（{names}{more}）", "info")
        self._start_download(
            images=jobs,
            skip_existing=not any_redown,
            mode="download",
            retries=3,
        )

    def _collect_gallery_conflicts(
        self, images: list, save_dir: str, *, title_override: str = None
    ) -> list:
        """即将下载的图序号，在图集目录里已有文件的路径列表。"""
        title = title_override or self._work_title("gallery")
        folder = gallery_folder_path(save_dir, title)
        if not os.path.isdir(folder):
            return []
        paths = []
        for it in images or []:
            name = str(it.get("name") or f"{int(it.get('index') or 0):04d}")
            for e in ("webp", "jpg", "jpeg", "png", "gif", "avif", "bmp"):
                p = os.path.join(folder, f"{name}.{e}")
                try:
                    if os.path.isfile(p) and os.path.getsize(p) > 256:
                        paths.append(p)
                except Exception:
                    log.debug("检查已存在文件失败 path=%s", p, exc_info=True)
        return paths

    def _start_download(
        self,
        images: list,
        *,
        skip_existing: bool = True,
        mode: str = "download",
        retries: int = 3,
    ):
        if self._dl_worker and self._dl_worker.isRunning():
            return
        if self._parse_worker and self._parse_worker.isRunning():
            self._log("解析尚未结束", "warn")
            return
        if not images:
            self._log("没有可下载的项目", "warn")
            return

        save_dir = self._save_root()
        if not (self.save_edit.text() or "").strip():
            self.save_edit.setText(save_dir.replace("\\", "/"))

        title = self._work_title("gallery")
        pending = getattr(self, "_conflict_decision", None)
        self._conflict_decision = None
        if isinstance(pending, dict) and pending.get("skip_existing") is not None:
            # 解析阶段已弹过窗
            title = pending.get("title") or title
            skip_existing = bool(pending.get("skip_existing"))
        else:
            check_imgs = list(images or [])
            for it in (self._info or {}).get("images") or []:
                if it not in check_imgs:
                    check_imgs.append(it)
            resolved = self._resolve_same_name(
                check_imgs,
                dialog_title="图集下载 · 发现同名文件",
                fallback_leaf="gallery",
            )
            if resolved is None:
                self._last_download_ok = False
                self._emit_nav_progress(False)
                return
            title = resolved.get("title") or title
            if resolved.get("skip_existing") is not None:
                skip_existing = bool(resolved["skip_existing"])
            if resolved.get("skip_existing") is False:
                self._mark_all_cards_for_redownload()
                images = self._selected_images() or list(
                    (self._info or {}).get("images") or images
                )
            elif resolved.get("skip_existing") is True and getattr(
                self, "_folder_title_override", None
            ):
                self._fill_name_cards((self._info or {}).get("images") or images)
                images = self._selected_images() or list(
                    (self._info or {}).get("images") or images
                )

        # 尾巴功能暂闭
        # self._tail_url_text = self.url_edit.text().strip()
        # self._tail_expected_count = len((self._info or {}).get("images") or images or [])

        self._cancel[0] = False
        self._dl_summary_set = False
        self.btn_sel_dl.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self._set_status("准备下载…", self._progress_gradient_color(0))

        fill_mode = bool(getattr(self, "_fill_only_names", None))
        if fill_mode:
            mode = "fill"
            skip_existing = True
            n_jobs = len(images or [])
            if n_jobs > 0:
                self._fill_planned_n = n_jobs
            self._fill_current_i = 0
            self._fill_ok_n = 0
            self._fill_err_n = 0
            self._fill_skip_n = 0
            self._fill_bubble(note="下载中", current=0)
        folder_override = ""
        if fill_mode:
            folder_override = (getattr(self, "_gallery_dir_override", None) or "").strip()

        self._emit_nav_progress(True, 0, mode="progress")
        self._dl_worker = GalleryDownloadWorker(
            images=images,
            save_dir=save_dir,
            title=title,
            cookie_path=self.ck_path.text().strip(),
            delay_ms=self._delay_ms(),
            skip_existing=skip_existing,
            cancel_flag=self._cancel,
            retries=retries,
            refresh_url=True,
            gallery_url=(self._info or {}).get("source_url") or "",
            mode=mode,
            folder_override=folder_override,
        )
        self._dl_worker.progress.connect(self._on_dl_progress)
        self._dl_worker.ok.connect(self._on_dl_ok)
        self._dl_worker.err.connect(self._on_dl_err)
        self._dl_worker.log.connect(self._log)
        self._dl_worker.done_detail.connect(self._on_dl_detail)
        self._dl_worker.item_done.connect(self._on_item_done)
        self._dl_worker.finished_clean.connect(self._on_dl_finished)
        self._dl_worker.start()

    def shutdown(self):
        """主窗口关窗时调用：打断解析/下载线程。"""
        from utils.qthread_util import stop_qthreads
        stop_qthreads(
            (getattr(self, "_parse_worker", None), getattr(self, "_dl_worker", None)),
            cancel_flag=getattr(self, "_cancel", None),
        )
        self._emit_nav_progress(False)

    def freeze_as_placeholder(self):
        """把本实例彻底冻结成不可交互的静态展示（用于「two」占位分页）。

        · 提前把 _cookie_auto_tried 标记为已尝试 → showEvent 不会再触发
          自动扫描/加载 Cookie（不会弹出「Cookie 文件加载」相关提示）。
        · 整个控件 setEnabled(False) → 任何点击/输入/粘贴都不会生效，
          不会解析、不会下载，也不会新建线程，与「e-hentai.org」页完全隔离。
        · 必须在构造完成后、加入 Tab 之前调用。
        """
        self._is_placeholder = True
        self._cookie_auto_tried = True
        self._auto_dl = False
        self.setEnabled(False)

    # ── 图源内容卡 ───────────────────────────────────────────────────────────

    def _clear_name_cards(self):
        for c in list(getattr(self, "_name_cards", []) or []):
            try:
                self._card_layout.removeWidget(c)
                c.setParent(None)
                c.deleteLater()
            except Exception:
                pass
        self._name_cards = []
        self._card_by_name = {}
        if getattr(self, "_empty_hint", None) is not None:
            try:
                self._card_layout.removeWidget(self._empty_hint)
                self._empty_hint.setParent(None)
                self._empty_hint.deleteLater()
            except Exception:
                pass
            self._empty_hint = None
        self._relayout_name_cards()

    def _show_empty_cards(self):
        self._clear_name_cards()
        self._set_status("粘贴链接 → 回车自动解析")

    def _fill_name_cards(self, images: list):
        self._clear_name_cards()
        folder = self._gallery_dir()
        done_n = 0
        sel_n = 0
        for it in images or []:
            card = GalleryNameCard(it)
            name = card._name
            if image_file_exists(folder, name):
                # 已在磁盘：灰色、不选中；需重下时用户再点亮
                card.set_downloaded(True, keep_selected=False)
                done_n += 1
            else:
                # 未下载：默认亮起选中
                card.set_downloaded(False)
                card.set_selected(True)
                sel_n += 1
            self._name_cards.append(card)
            self._card_by_name[name] = card
            self._card_layout.addWidget(card)
        total = len(self._name_cards)
        if total:
            self._set_status(
                f"已下 {done_n}/{total}",
                self._done_ratio_color(done_n, total),
            )
        else:
            self._set_status("")
        self._toggle_hide_done()  # 内部会 _relayout_name_cards

    def _refresh_card_states(self, failed_names=None):
        """按磁盘状态刷新。

        · 已在磁盘：灰卡；若用户已点亮则保留（准备重下）
        · 未在磁盘（含失败）：始终亮起，不可点灭
        """
        failed_names = set(failed_names or [])
        folder = self._gallery_dir()
        done_n = 0
        sel_n = 0
        for name, card in (self._card_by_name or {}).items():
            was_sel = card.is_selected()
            if image_file_exists(folder, name):
                # 已下载：若用户仍保持选中则继续亮（准备重下），否则变灰
                card.set_downloaded(True, keep_selected=was_sel)
                done_n += 1
            elif name in failed_names:
                card.set_failed(True)  # 内部会 selected=True、不可点灭
            else:
                # 未下载：强制亮起（取消「点灭后保持熄灭」的旧逻辑）
                card.set_downloaded(False)
            if card.is_selected():
                sel_n += 1
        total = len(self._card_by_name)
        if total:
            self._set_status(
                f"已下 {done_n}/{total}",
                self._done_ratio_color(done_n, total),
            )

    def _on_item_done(self, name: str):
        card = (self._card_by_name or {}).get(name)
        if card is not None:
            card.set_downloaded(True, keep_selected=False)
        if self._card_by_name:
            done_n = sum(
                1 for c in self._card_by_name.values()
                if getattr(c, "_downloaded", False)
            )
            total = len(self._card_by_name)
            pct = int(done_n * 100 / max(1, total))
            self._set_status(
                f"{pct}% · 已下 {done_n}/{total}",
                self._progress_gradient_color(pct),
            )
        # 序列补全气泡：单张成功/跳过累加
        if getattr(self, "_fill_only_names", None) is not None or getattr(
            self, "_fill_active_once", False
        ):
            self._fill_ok_n = int(getattr(self, "_fill_ok_n", 0) or 0) + 1
            planned = int(getattr(self, "_fill_planned_n", 0) or 0)
            cur = min(self._fill_ok_n + int(getattr(self, "_fill_err_n", 0) or 0), planned or self._fill_ok_n)
            self._fill_current_i = cur
            self._fill_bubble(note="下载中", current=cur)

    def _on_dl_progress(self, pct: int, text: str):
        try:
            pct = max(0, min(100, int(pct)))
        except (TypeError, ValueError):
            pct = 0
        self._on_progress_value(pct)
        body = (text or "下载中…").strip()
        if not str(body).startswith(f"{pct}%"):
            body = f"{pct}% · {body}"
        self._set_status(body, self._progress_gradient_color(pct))
        self._emit_nav_progress(True, pct, mode="progress")
        # 序列补全：从「补全 2/3」同步气泡进度
        if getattr(self, "_fill_only_names", None) is not None or getattr(
            self, "_fill_active_once", False
        ):
            m = re.search(r"(\d+)\s*/\s*(\d+)", text or "")
            if m:
                try:
                    cur = int(m.group(1))
                    tot = int(m.group(2))
                except ValueError:
                    cur, tot = 0, 0
                if tot > 0:
                    self._fill_current_i = cur
                    if tot != int(getattr(self, "_fill_planned_n", 0) or 0):
                        self._fill_planned_n = tot
                    self._fill_bubble(note="下载中", current=cur, total=tot)

    def _on_dl_ok(self, folder: str):
        # 校验以 _on_dl_detail 为准（detail 先于 ok）；此处仅兜底未出 detail 的情况
        if not getattr(self, "_dl_summary_set", False):
            self._last_download_ok = True
            self._last_batch_note = "成功"
        self._on_progress_value(100)
        # 状态行由 _on_dl_detail 写最终总结（ok 信号晚于 detail，勿覆盖）
        if not getattr(self, "_dl_summary_set", False):
            self._set_status("完成：已保存", tk("ok"))
        self._log(f"✓ 已保存到：{folder}", "ok")
        _gallery_commit_download_record(self)
        self._emit_nav_progress(True, 100, mode="progress")
        # 补全/批处理：走常驻气泡，不弹独立「完成下载」
        if getattr(self, "_fill_only_names", None) is not None or getattr(
            self, "_fill_active_once", False
        ):
            pass
        elif not in_batch_download(self):
            show_cursor_toast("下载", "完成", accent="ok")
        elif self._batch_mode:
            note = "成功" if getattr(self, "_last_download_ok", False) else "失败"
            total = len(self._batch_urls or [])
            if total:
                show_batch_progress_toast(
                    getattr(self, "_batch_platform_label", None) or self._BATCH_LABEL,
                    self._batch_index,
                    total,
                    note=note,
                )
        else:
            note = "成功" if getattr(self, "_last_download_ok", False) else "失败"
            set_batch_progress_note(note)

    def _on_dl_err(self, msg: str):
        self._last_download_ok = False
        self._last_batch_note = "失败"
        self._dl_summary_set = False
        self._set_status("失败 / 取消", tk("err"))
        self._log(f"✗ {msg}", "err")
        self._emit_nav_progress(False)
        if self._batch_mode:
            self._batch_err += 1
            self._batch_schedule_next(2000)
            total = len(self._batch_urls or [])
            if total:
                show_batch_progress_toast(
                    getattr(self, "_batch_platform_label", None) or self._BATCH_LABEL,
                    self._batch_index,
                    total,
                    note="失败",
                )
        else:
            # 自动下载：失败也要显式上报，保留记录行
            if not self._report_unified_batch_result(False, note="失败"):
                if in_batch_download(self):
                    set_batch_progress_note("失败")

    def _on_dl_detail(self, detail: dict):
        """记录失败项，并把刷新后的 url 写回 _info，方便下次补全。"""
        failed = list(detail.get("failed") or [])
        self._last_failed = failed
        # 把成功刷新过的 url 合并回主列表（worker 用的是拷贝，失败项带最新 url）
        if self._info and self._info.get("images"):
            by_name = {
                (it.get("name") or ""): it
                for it in self._info["images"]
            }
            for it in failed:
                nm = it.get("name") or ""
                if nm and nm in by_name and it.get("url"):
                    by_name[nm]["url"] = it["url"]
        failed_names = [
            (it.get("name") or "") for it in failed if it.get("name")
        ]
        self._refresh_card_states(failed_names=failed_names)
        miss = self._missing_list()
        ok_n = int(detail.get("ok_n") or 0)
        skip_n = int(detail.get("skip_n") or 0)
        fail_n = int(detail.get("fail_n") or 0)
        miss_n = len(miss)
        fill_mode = getattr(self, "_fill_only_names", None) is not None
        # 定点补全：按「本次计划序号是否已在盘」判定，避免误伤
        if fill_mode:
            folder = self._gallery_dir()
            still_fill = 0
            for miss_name in (self._fill_only_names or set()):
                stem = os.path.splitext(str(miss_name).strip())[0]
                if not image_file_exists(folder, stem) and not image_file_exists(
                    folder, str(miss_name).strip()
                ):
                    still_fill += 1
            miss_n = still_fill
            # 成功：无下载失败，且计划序号都已落盘；或至少成功了一些且无 fail
            validated = fail_n == 0 and still_fill == 0 and (ok_n + skip_n) > 0
            if not validated and fail_n == 0 and still_fill == 0 and (ok_n + skip_n) == 0:
                # 全是 skip 且盘上已齐
                validated = still_fill == 0
            self._last_download_ok = validated
            self._last_batch_note = "成功" if validated else (
                "失败" if fail_n else f"未齐·仍缺{still_fill}"
            )
            # 同步气泡成/跳/败（ok_n 含 skip）
            pure_ok = max(0, int(ok_n) - int(skip_n))
            self._fill_ok_n = pure_ok
            self._fill_skip_n = int(skip_n)
            self._fill_err_n = int(fail_n)
            planned = int(getattr(self, "_fill_planned_n", 0) or 0) or max(
                1, pure_ok + int(skip_n) + int(fail_n)
            )
            note = "成功" if validated else ("失败" if fail_n else "未齐")
            self._fill_bubble(note=note, current=planned, total=planned)
            if fail_n:
                summary = f"补全：成功 {ok_n} · 失败 {fail_n} · 仍缺 {still_fill}"
                color = tk("warn")
            elif still_fill:
                summary = f"补全：成功 {ok_n} · 仍缺 {still_fill} 张"
                color = tk("warn")
            else:
                summary = f"补全完成：{ok_n + skip_n} 张 · 已齐"
                color = tk("ok")
            self._set_status(summary, color)
            self._dl_summary_set = True
            actual = (detail.get("folder") or "").strip()
            if actual and os.path.isdir(actual):
                self._fill_actual_folder = actual
            # 立刻按盘面写回仍缺项（不拿本次子集 images 覆盖整本）
            try:
                self._maybe_delete_fill_report()
            except Exception:
                pass
        else:
            # 校验成功：无失败、无缺失，且至少有成功/跳过项 → 才允许删记录
            validated = (fail_n == 0 and miss_n == 0 and (ok_n + skip_n) > 0)
            self._last_download_ok = validated
            self._last_batch_note = "成功" if validated else "失败"
            if fail_n:
                summary = f"完成：成功 {ok_n} · 失败 {fail_n} · 仍缺 {miss_n} 张"
                color = tk("warn")
            elif miss_n:
                if skip_n:
                    summary = f"完成：成功 {ok_n}（跳过 {skip_n}）· 仍缺 {miss_n} 张"
                else:
                    summary = f"完成：成功 {ok_n} · 仍缺 {miss_n} 张"
                color = tk("warn")
            elif skip_n:
                summary = f"完成：成功 {ok_n} 张（跳过 {skip_n}）· 已齐全"
                color = tk("ok")
            else:
                summary = f"完成：成功 {ok_n} 张 · 已齐全"
                color = tk("ok")
            self._set_status(summary, color)
            self._dl_summary_set = True

            if miss and self._info:
                folder = self._gallery_dir()
                rp = write_missing_report(self._info, folder, ok_n=ok_n)
                if rp:
                    self._log(f"📄 缺失清单已保存：{os.path.basename(rp)}", "warn")
            else:
                # 全部齐全：清除旧报告
                old_rp = os.path.join(self._gallery_dir(), MISSING_REPORT_NAME)
                if os.path.isfile(old_rp):
                    try:
                        os.remove(old_rp)
                    except Exception:
                        pass

        if self._batch_mode:
            if validated:
                self._batch_ok += 1
                self._batch_remove_current_record_line()
            else:
                self._batch_err += 1
            self._batch_schedule_next(3000)

    def _on_dl_finished(self):
        self.btn_cancel.setEnabled(False)
        self._refresh_card_states(
            failed_names=[
                (it.get("name") or "") for it in (self._last_failed or [])
            ]
        )
        self._update_action_buttons()
        # 序列补全：下载线程结束后收口（提示 + 切回速存）
        fill_now = getattr(self, "_fill_only_names", None) is not None or getattr(
            self, "_fill_active_once", False
        )
        if fill_now:
            ok = bool(getattr(self, "_last_download_ok", False))
            note = (getattr(self, "_last_batch_note", None) or "").strip()
            fail_n = len(getattr(self, "_last_failed", None) or [])
            planned = int(getattr(self, "_fill_planned_n", 0) or 0) or len(
                getattr(self, "_fill_only_names", None) or []
            )
            if ok:
                summary = f"补全成功 · {planned} 项" if planned else "补全成功"
                detail = note if note and note not in ("成功",) else ""
            else:
                summary = "补全失败" if fail_n or note == "失败" else "补全未完成"
                if planned:
                    summary = f"{summary} · 计划 {planned} 项"
                detail = note if note and note not in ("失败",) else ""
                if fail_n:
                    detail = (detail + "\n" if detail else "") + f"失败 {fail_n} 张"
            # 延后一拍，避免与信号栈重入；内部会删清单并 clear
            QTimer.singleShot(
                0,
                lambda o=ok, s=summary, d=detail: self._finish_sequence_fill(
                    ok=o, summary=s, detail=d
                ),
            )
        # QTimer.singleShot(1000, self._clear_url_after_gallery_tail)  # 尾巴暂闭
        # 下载线程结束再兜一次：ok 信号漏了或只写下了部分，也把防重名写上
        note = (getattr(self, "_last_batch_note", None) or "").strip()
        if getattr(self, "_last_download_ok", False) or note in ("成功", "已下载过"):
            _gallery_commit_download_record(self)
        # 任务结束：侧栏进度线消失 + 自动下载显式结算（校验通过才删行）
        self._emit_nav_progress(False)
        if not getattr(self, "_batch_mode", False):
            self._report_unified_batch_result()

    def _clear_url_after_gallery_tail(self):
        images = list((self._info or {}).get("images") or [])
        expected = int(getattr(self, "_tail_expected_count", 0) or len(images))
        if expected <= 0 or not images:
            return
        folder = self._gallery_dir()
        done_n = 0
        for it in images:
            name = str(it.get("name") or f"{int(it.get('index') or 0):04d}")
            if image_file_exists(folder, name):
                done_n += 1
        if done_n >= expected and self.url_edit.text().strip() == getattr(self, "_tail_url_text", ""):
            self.url_edit.clear()
            self._clear_name_cards()
            self._tail_url_text = ""
            self._tail_expected_count = 0
            self._log(
                f"下载尾巴：已确认目录图片齐全（{done_n}/{expected}），清空当前链接与图源列表，避免重复自动下载。",
                "ok",
            )


def _paint_eh_icon(size: int = 20, selected: bool = True, dark: bool = True) -> QIcon:
    """「e-hentai.org」分页徽章：简约地球/链接符号，呼应「网址图集」定位。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)

    s = float(size)
    m = s * 0.10
    body = QRectF(m, m, s - 2 * m, s - 2 * m)

    if selected:
        ring = QColor("#7C9CF2") if dark else QColor("#4C6EF5")
        fill = QColor(ring)
        fill.setAlpha(34 if dark else 44)
    else:
        ring = QColor("#6b7280") if dark else QColor("#9aa3af")
        ring.setAlpha(160)
        fill = QColor(ring)
        fill.setAlpha(18)

    p.setPen(QPen(ring, max(1.1, s * 0.07)))
    p.setBrush(QBrush(fill))
    p.drawEllipse(body)

    # 经线（竖直）+ 纬线（水平）+ 一条收窄竖椭圆，模拟地球经纬网
    cx, cy = body.center().x(), body.center().y()
    p.drawLine(QPointF(cx, body.top()), QPointF(cx, body.bottom()))
    p.drawLine(QPointF(body.left(), cy), QPointF(body.right(), cy))
    p.setBrush(Qt.NoBrush)
    lon = QRectF(body.left() + body.width() * 0.24, body.top(),
                 body.width() * 0.52, body.height())
    p.drawEllipse(lon)

    p.end()
    return QIcon(pm)


def _paint_gallery_two_icon(size: int = 20, selected: bool = True, dark: bool = True) -> QIcon:
    """「two」分页徽章：叠层复制符号，呼应「复制一份」的定位。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)

    s = float(size)

    if selected:
        back = QColor("#A78BFA") if dark else QColor("#8B6CF0")
        back.setAlpha(120)
        front_pen = QColor("#A78BFA") if dark else QColor("#7C5CF0")
        front_fill = QColor(front_pen)
        front_fill.setAlpha(34 if dark else 44)
    else:
        back = QColor("#6b7280")
        back.setAlpha(90)
        front_pen = QColor("#6b7280") if dark else QColor("#9aa3af")
        front_pen.setAlpha(170)
        front_fill = QColor(front_pen)
        front_fill.setAlpha(18)

    back_rect = QRectF(s * 0.32, s * 0.10, s * 0.56, s * 0.56)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(back))
    p.drawRoundedRect(back_rect, s * 0.12, s * 0.12)

    front_rect = QRectF(s * 0.12, s * 0.34, s * 0.56, s * 0.56)
    p.setPen(QPen(front_pen, max(1.1, s * 0.07)))
    p.setBrush(QBrush(front_fill))
    p.drawRoundedRect(front_rect, s * 0.12, s * 0.12)

    p.end()
    return QIcon(pm)


def _paint_pixiv_icon(size: int = 20, selected: bool = True, dark: bool = True) -> QIcon:
    """Pixiv 分页徽章：画笔斜线，呼应用户生成内容平台。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)

    s = float(size)
    if selected:
        pen_c = QColor("#3B82F6") if dark else QColor("#2563EB")
        fill_c = QColor(pen_c)
        fill_c.setAlpha(34 if dark else 44)
    else:
        pen_c = QColor("#6b7280") if dark else QColor("#9aa3af")
        pen_c.setAlpha(160)
        fill_c = QColor(pen_c)
        fill_c.setAlpha(18)

    pen = QPen(pen_c, max(1.2, s * 0.08))
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(QBrush(fill_c))

    x1, y1 = s * 0.22, s * 0.75
    x2, y2 = s * 0.78, s * 0.25
    p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    p.drawEllipse(QPointF(x1, y1), s * 0.09, s * 0.09)
    p.drawEllipse(QPointF(x2, y2), s * 0.09, s * 0.09)

    p.setBrush(Qt.NoBrush)
    mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
    p.drawEllipse(QPointF(mid_x, mid_y), s * 0.15, s * 0.15)

    p.end()
    return QIcon(pm)


def _apply_gallery_tab_bg(widget, object_name: str):
    """子页底色对齐 Tab pane（panel_2）。"""
    widget.setObjectName(object_name)
    widget.setAttribute(Qt.WA_StyledBackground, True)
    widget.setAutoFillBackground(True)
    widget.setStyleSheet(
        f"#{object_name}{{background:{tk('panel_2')};border:none;}}"
    )


class _GalleryPixivPage(QWidget):
    """Pixiv 作品页：解析 + 下载（参考 e-hentai 流程）。"""

    nav_progress = pyqtSignal(bool, int, str)
    # 本页批处理气泡前缀 ZZ
    _BATCH_LABEL = "pixiv"
    _BATCH_ENGINE_KEY = "pixiv"

    def __init__(self):
        super().__init__()
        self._info = None
        self._parse_worker = None
        self._dl_worker = None
        self._cancel = [False]
        self._auto_dl = False
        self._cookie_auto_tried = False
        self._cookie_loaded_via = None
        self._last_failed = []
        self._name_cards = []
        self._card_by_name = {}
        self._nav_busy = False
        self._tail_url_text = ""
        self._tail_expected_count = 0
        self._is_placeholder = False
        self._inline_blocks = {}
        self._last_download_ok = False  # 供自动下载引擎判断本次下载是否成功
        # 序列文件检查 → 补全
        self._fill_only_names = None
        self._fill_report_path = ""
        self._gallery_dir_override = ""
        self._bypass_record = False
        # 批处理状态
        self._batch_urls = []
        self._batch_index = 0
        self._batch_mode = False
        self._batch_ok = 0
        self._batch_err = 0
        self._batch_skip = 0
        self._batch_file_path = ""
        self._batch_stop_requested = False  # F6：当前条完成后中止
        self._batch_platform_label = self._BATCH_LABEL
        # pixiv 无「已下载记录」；hitomi 子类会改 _record_key 并显示版块
        self._record_key = ""
        self._batch_timer = QTimer(self)
        self._batch_timer.setSingleShot(True)
        self._batch_timer.timeout.connect(self._batch_next)

        theme.changed.connect(self.refresh_theme)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        # ══ 解析与下载 ═══════════════════════════════════════════════════════
        gb = make_card("CardGalleryParse", borderless=True)
        gv = QVBoxLayout(gb)
        gv.setSpacing(0)
        gv.setContentsMargins(0, max(0, CARD_TOP_GAP - 6), 0, CARD_BOTTOM_GAP)
        self._theme_titles = []
        self._func_cards = [gb]

        body = QWidget()
        body.setObjectName("GalleryParseBody")
        body.setStyleSheet("#GalleryParseBody{background:transparent;border:none;}")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(7)
        gv.addWidget(body, 1)

        # Cookie / 保存位置控件（由「关于」弹窗统一管理，页面 UI 隐藏）
        self.ck_status = _ElideLabel("")
        self.ck_status.setObjectName("StatusLbl")
        self.ck_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.ck_status.setStyleSheet(f"color:{tk('text_faint')}; font-size:12px;")
        self.ck_status.setVisible(False)
        self.btn_ck = QPushButton("选择文件")
        self.btn_ck.setObjectName("BtnSmall")
        self.btn_ck.setMinimumWidth(72)
        self.btn_ck.clicked.connect(self._pick_cookie)
        self.btn_ck.setVisible(False)
        self.ck_path = QLineEdit()
        self.ck_path.setPlaceholderText("自动扫描下载目录…")
        self.ck_path.setReadOnly(True)
        self.ck_path.setMinimumWidth(0)
        self.ck_path.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._ck_icon = apply_folder_path_edit(self.ck_path)
        self.ck_path.textChanged.connect(self._on_cookie_change)
        self.ck_path.setVisible(False)
        self.save_edit = QLineEdit(
            os.path.join(os.path.expanduser("~"), "Downloads").replace("\\", "/")
        )
        self.save_edit.setMinimumWidth(0)
        self.save_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._sv_icon = apply_folder_path_edit(self.save_edit)
        self.save_edit.setVisible(False)

        # ── 第1行：链接输入 + 取消 ──
        row1 = QHBoxLayout()
        row1.setSpacing(10)

        url_wrap = QWidget()
        url_wrap.setAttribute(Qt.WA_StyledBackground, True)
        url_wrap.setStyleSheet("background: transparent;")
        url_wrap_lay = QVBoxLayout(url_wrap)
        url_wrap_lay.setContentsMargins(0, 0, 0, 0)
        url_wrap_lay.setSpacing(0)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("粘贴 pixiv 作品链接 → 回车自动解析下载")
        self.url_edit.setFixedHeight(MEDIUM_BUTTON_H)
        self._url_icon = apply_folder_path_edit(self.url_edit)
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
        self.btn_cancel.clicked.connect(lambda: self._cancel.__setitem__(0, True))
        row1.addWidget(self.btn_cancel, 0, Qt.AlignTop)
        bl.addLayout(row1)

        # ── 第2行：提示（pixiv 无下载记录；hitomi 在页底显示）──
        row2 = QHBoxLayout()
        row2.setSpacing(10)

        self.lbl_hint = QLabel("粘贴链接 → 回车自动解析")
        self.lbl_hint.setFixedHeight(MEDIUM_BUTTON_H)
        self.lbl_hint.setAlignment(Qt.AlignVCenter)
        row2.addWidget(self.lbl_hint, 1)
        bl.addLayout(row2)

        root.addWidget(gb, 0)

        # ══ 图源列表（小号内容卡 · 高度约占页面 30%）══════════════════════════
        media_head = QHBoxLayout()
        lbl_media = QLabel("图源列表")
        lbl_media.setObjectName("SecTitle")
        media_head.addWidget(lbl_media)
        media_head.addStretch(1)
        self.chk_hide_done = QCheckBox("隐藏完成")
        self.chk_hide_done.setStyleSheet("background:transparent; font-size:12px;")
        self.chk_hide_done.toggled.connect(self._toggle_hide_done)
        media_head.addWidget(self.chk_hide_done)
        self.btn_sel_dl = QPushButton("选中下载")
        self.btn_sel_dl.setObjectName("BtnSmall")
        self.btn_sel_dl.setMinimumWidth(84)
        self.btn_sel_dl.setEnabled(False)
        self.btn_sel_dl.setStyleSheet("QPushButton{padding:2px 10px;} QPushButton:disabled{color:#555555;background:transparent;border-color:#333333;}")
        self.btn_sel_dl.clicked.connect(self._download_selected)
        media_head.addWidget(self.btn_sel_dl)
        self.btn_open_dir = QPushButton("打开目录")
        self.btn_open_dir.setObjectName("BtnSmall")
        self.btn_open_dir.setMinimumWidth(84)
        self.btn_open_dir.setEnabled(True)
        self.btn_open_dir.setStyleSheet("QPushButton{padding:2px 10px;} QPushButton:disabled{color:#555555;background:transparent;border-color:#333333;}")
        self.btn_open_dir.clicked.connect(self._open_save_dir)
        media_head.addWidget(self.btn_open_dir)
        root.addLayout(media_head)

        self.card_scroll = QScrollArea()
        self.card_scroll.setWidgetResizable(True)
        self.card_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.card_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.card_scroll.setMinimumHeight(GalleryNameCard.CARD_H * 3 + 16)
        self.card_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.card_scroll.setFrameShape(QFrame.NoFrame)
        self.card_scroll.setObjectName("GalleryCardScroll")
        self.card_scroll.viewport().setAutoFillBackground(False)
        self.card_scroll.setStyleSheet(
            "QScrollArea#GalleryCardScroll{background:transparent;border:none;}"
            "QScrollArea#GalleryCardScroll > QWidget > QWidget{background:transparent;}"
        )
        card_inner = QWidget()
        card_inner.setObjectName("GalleryCardInner")
        card_inner.setAutoFillBackground(False)
        card_inner.setStyleSheet("#GalleryCardInner{background:transparent;border:none;}")
        self._card_layout = FlowLayout(card_inner, margin=4, h_spacing=6, v_spacing=6)
        self.card_scroll.setWidget(card_inner)
        # 高度在 resizeEvent 里按页面 30% 设定
        root.addWidget(self.card_scroll, 0)

        self._empty_hint = None
        self._show_empty_cards()

        # ══ 运行日志 ═══════════════════════════════════════════════════════
        card_log = make_card("CardGalleryLog", borderless=True)
        log_lay = QVBoxLayout(card_log)
        log_lay.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        log_lay.setSpacing(0)
        title_log = install_card_title(card_log, log_lay, "运行日志")
        self._theme_titles.append(title_log)
        self._func_cards.append(card_log)

        head = title_log.parentWidget()
        head_l = head.layout() if head else None
        if head_l is not None:
            head_l.removeWidget(title_log)
            title_row = QHBoxLayout()
            title_row.setContentsMargins(0, 0, 0, 0)
            title_row.setSpacing(6)
            title_row.addWidget(title_log, 0, Qt.AlignVCenter)
            title_row.addStretch(1)
            self.btn_clear_log = apply_mini_button(QPushButton("清除记录"))
            self.btn_clear_log.clicked.connect(lambda: _gallery_log_clear(self))
            title_row.addWidget(self.btn_clear_log, 0, Qt.AlignVCenter)
            head_l.addLayout(title_row)

        self.log_box = QTextEdit()
        self.log_box.setObjectName("GalleryLogBox")
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(72)
        self.log_box.setFrameShape(QFrame.NoFrame)
        apply_simple_record(self.log_box)
        _gallery_log_bind(self, self.log_box)
        log_lay.addWidget(self.log_box, 1)
        root.addWidget(card_log, 1)

        # 页底：防重复下载记录（pixiv 隐藏，hitomi 再打开）
        root.addWidget(_build_gallery_record_bar(self, visible=False), 0)

        if not HAS_REQUESTS:
            QTimer.singleShot(0, lambda: self._log(
                "未检测到 requests，请 pip install requests", "err"
            ))
        QTimer.singleShot(0, self._sync_card_strip_height)

        self._gallery_root = root

    # ── 尺寸 / 主题 / 日志 ──────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_card_strip_height()

    def _sync_card_strip_height(self):
        sc = getattr(self, "card_scroll", None)
        if sc is None:
            return
        h = self.height()
        if h <= 0:
            return
        target = int(h * 0.30)
        lo = GalleryNameCard.CARD_H * 3 + 16
        hi = max(lo, int(h * 0.50))
        target = max(lo, min(hi, target))
        if sc.height() != target:
            sc.setFixedHeight(target)
        self._relayout_name_cards()

    def _relayout_name_cards(self):
        """批量增删内容卡后强制刷新滚动区内容高度（与 EH 页同因）。"""
        sc = getattr(self, "card_scroll", None)
        lay = getattr(self, "_card_layout", None)
        if sc is None or lay is None:
            return
        inner = sc.widget()
        if inner is None:
            return
        lay.invalidate()
        for _ in range(2):
            w = max(1, sc.viewport().width() or sc.width() or inner.width() or 1)
            need_h = max(0, int(lay.heightForWidth(w)))
            if inner.minimumHeight() != need_h:
                inner.setMinimumHeight(need_h)
            inner.updateGeometry()

    def refresh_theme(self, *_):
        restyle_folder_path_edit(self.ck_path, getattr(self, "_ck_icon", None))
        restyle_folder_path_edit(self.save_edit, getattr(self, "_sv_icon", None))
        restyle_folder_path_edit(self.url_edit, getattr(self, "_url_icon", None))
        for lbl in self._theme_titles:
            restyle_card_title(lbl)
        for frame in self._func_cards:
            restyle_card_frame(frame)
        for c in getattr(self, "_name_cards", []):
            if hasattr(c, "refresh_theme"):
                c.refresh_theme()
        path = (self.ck_path.text() or "").strip() if hasattr(self, "ck_path") else ""
        self._set_cookie_btn_loaded(bool(path and os.path.isfile(path)))

    def _log(self, msg, level="info"):
        _gallery_log_append(self, msg, level)

    def _scroll_log_bottom(self):
        _gallery_log_scroll_bottom(self)

    def _project_dir_name(self) -> str:
        """当前作品下载目录名（与落盘文件夹一致，不含路径）。"""
        ov = getattr(self, "_folder_title_override", None)
        raw = str(ov or "").strip() or str((self._info or {}).get("title") or "").strip()
        if not raw:
            return ""
        return _safe_folder_name(raw, "gallery")

    def _with_project(self, text: str) -> str:
        return _append_project_name(text, self._project_dir_name())

    def _set_status(self, text, color=None):
        """状态提示行：下载相关必须醒目，有色时加粗。

        过程中/完成后自动追加项目名（下载目录名），如「已下 2/2 · 无题_17」。
        """
        if hasattr(self, "lbl_hint") and self.lbl_hint is not None:
            body = text or ""
            if _status_needs_project(body):
                body = self._with_project(body)
            if self._batch_mode and self._batch_urls:
                idx = self._batch_index
                total = len(self._batch_urls)
                display = f"进度 {idx}/{total}：" + body
            else:
                display = body
            self.lbl_hint.setText(display)
            col = color or tk("text_mut")
            weight = "700" if color else "600"
            self.lbl_hint.setStyleSheet(
                f"color:{col}; font-weight:{weight}; font-size:13px;"
            )

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

    def _done_ratio_color(self, done_n: int, total: int) -> str:
        """已下/总数 统计色：全完成绿、未开始橙、进行中走进度渐变。"""
        total = max(0, int(total or 0))
        done_n = max(0, int(done_n or 0))
        if total <= 0:
            return tk("text_mut")
        if done_n >= total:
            return tk("ok")
        if done_n <= 0:
            return tk("warn")
        return self._progress_gradient_color(int(done_n * 100 / total))

    def _on_progress_value(self, value):
        # 空闲时不刷新线，避免任务结束后被残留进度值再次点亮
        if not getattr(self, "_nav_busy", False):
            return
        color = self._progress_gradient_color(value)
        try:
            if hasattr(self, "_dl_progress_line") and self._dl_progress_line is not None:
                self._dl_progress_line.set_progress(value, color)
        except Exception:
            pass
        if not (self._parse_worker and self._parse_worker.isRunning()):
            self._emit_nav_progress(True, int(value), mode="progress")

    def _emit_nav_progress(self, active: bool, pct: int = 0, mode: str = "progress"):
        """页内简易进度线 + 侧栏进度线统一入口（pixiv / hitomi 共用）。

        显示：busy=解析灰线 / progress=下载彩色。
        隐藏：必须 active=False（完成/失败/取消/已下载过/无任务）。
        """
        self._nav_busy = bool(active)
        line = getattr(self, "_dl_progress_line", None)
        if not active:
            try:
                self.nav_progress.emit(False, 0, "")
            except Exception:
                pass
            try:
                if line is not None:
                    line.hide_line()
            except Exception:
                pass
            return
        if mode == "busy":
            try:
                self.nav_progress.emit(True, -1, "#94a3b8")
            except Exception:
                pass
            try:
                if line is not None:
                    line.set_busy(True)
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
            if line is not None:
                line.set_progress(pct, color)
        except Exception:
            pass

    # ── Cookie / 目录 ─────────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        if self._cookie_auto_tried:
            return
        self._cookie_auto_tried = True
        QTimer.singleShot(0, self._auto_load_cookie)

    def _set_cookie_btn_loaded(self, loaded: bool):
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
        cur = (self.ck_path.text() or "").strip()
        if cur and os.path.isfile(cur):
            self._on_cookie_change(cur)
            return
        dirs = []
        if hasattr(self, "save_edit"):
            save_dir = (self.save_edit.text() or "").strip()
            if save_dir:
                dirs.append(save_dir)
        for d in _default_download_dirs():
            dirs.append(d)
        path = find_pixiv_cookie_file(dirs)
        if path:
            self._cookie_loaded_via = "auto"
            self.ck_path.setText(path.replace("\\", "/"))
            name = os.path.basename(path)
            self._log(f"✓ 已自动从下载目录加载 Cookie「{name}」", "ok")
            self._set_status("Cookie 已自动加载 ✓", tk("ok"))
        else:
            self._cookie_loaded_via = None
            self._set_cookie_btn_loaded(False)
            self._log("下载目录未找到 pixiv Cookie，请点「选择文件」手动指定", "warn")

    def _pick_cookie(self):
        start = (self.ck_path.text() or "").strip()
        if start and os.path.isfile(start):
            start = os.path.dirname(start)
        elif hasattr(self, "save_edit") and self.save_edit.text().strip():
            start = self.save_edit.text().strip()
        else:
            dd = _default_download_dirs()
            start = dd[0] if dd else ""
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
        has_pixiv = "pixiv.net" in low or ".pixiv." in low
        lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
        count = len(lines)
        strong = any(k in text for k in ("PHPSESSID", "device_token", "tag_view_ranking"))
        if not has_pixiv:
            self.ck_status.setFullText("⚠ 文件中未见 pixiv.net 域")
            self.ck_status.setStyleSheet(f"color:{tk('warn')}; font-size:12px;")
            self._set_cookie_btn_loaded(True)
            return
        if strong:
            msg = f"✅ Cookie 有效（{count} 行，含登录字段）" if not via_auto else f"✅ 已自动加载（{count} 行）"
            self.ck_status.setStyleSheet(f"color:{tk('ok')}; font-size:12px;")
            self._set_cookie_btn_loaded(True)
        elif count > 0:
            msg = "⚠ 可能缺少登录字段（PHPSESSID 等）"
            self.ck_status.setStyleSheet(f"color:{tk('warn')}; font-size:12px;")
            self._set_cookie_btn_loaded(True)
        else:
            msg = "⚠ Cookie 文件为空"
            self.ck_status.setStyleSheet(f"color:{tk('warn')}; font-size:12px;")
            self._set_cookie_btn_loaded(False)
        self.ck_status.setFullText(msg)

    def _choose_dir(self):
        d = QFileDialog.getExistingDirectory(
            self, "选择保存目录",
            self.save_edit.text().strip() or os.path.expanduser("~"),
        )
        if d:
            self.save_edit.setText(d.replace("\\", "/"))

    def _show_help(self):
        from styles.style_all import message_box_info
        message_box_info(
            self,
            "Pixiv 下载说明",
            "1. 粘贴 pixiv 作品链接（artworks/数字），自动解析图片并下载\n\n"
            "2. Cookie：用扩展导出 Netscape cookies.txt\n"
            "   推荐文件名 www.pixiv.net_cookies.txt，放入下载目录自动加载\n"
            "   多数作品需要登录才能查看原图\n\n"
            "3. 内容卡亮起 = 待下载，变灰 = 已下载，点击灰卡可重新亮起重下\n\n"
            "4. 请控制下载频率，避免触发 pixiv 限制",
        )

    def _find_page_video_engine(self):
        """查找主窗口上的 PageVideo（自动下载引擎）。"""
        w = self
        while w is not None:
            pv = getattr(w, "page_douyin", None)
            if pv is not None and hasattr(pv, "start_batch_from_file"):
                return pv, w
            try:
                w = w.parent()
            except Exception:
                break
        return None, None

    def _batch_choose_file(self):
        """图集「批处理」：选文件后统一走自动下载（支持混链）。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择批处理文件", os.path.expanduser("~"),
            "文本文件 (*.txt);;所有文件 (*)"
        )
        if not path:
            return
        pv, mw = self._find_page_video_engine()
        if pv is None:
            self._log("未找到自动下载引擎，无法启动批处理", "err")
            self._set_status("自动下载引擎不可用", tk("err"))
            return
        if mw is not None and hasattr(mw, "stack") and hasattr(mw, "btn_douyin"):
            try:
                idx = mw.stack.indexOf(pv)
                if idx >= 0:
                    mw._switch(idx, mw.btn_douyin)
            except Exception:
                pass
        self._log("批处理 → 自动下载（混链按平台分发）…", "ok")
        if not pv.start_batch_from_file(path):
            self._log("自动下载未能启动", "warn")
            self._set_status("自动下载未启动", tk("warn"))

    def request_batch_stop(self) -> bool:
        """F6：优先转自动下载引擎中止；无则处理本页遗留本地批。"""
        pv, _mw = self._find_page_video_engine()
        if pv is not None and getattr(pv, "_batch_mode", False):
            if hasattr(pv, "request_batch_stop"):
                return bool(pv.request_batch_stop())
            return False
        if not getattr(self, "_batch_mode", False):
            return False
        if getattr(self, "_batch_stop_requested", False):
            set_batch_progress_note("已预约中止")
            return True
        self._batch_stop_requested = True
        busy = False
        for attr in ("_dl_worker", "_parse_worker"):
            w = getattr(self, attr, None)
            try:
                if w is not None and w.isRunning():
                    busy = True
                    break
            except Exception:
                pass
        if not busy:
            try:
                self._batch_timer.stop()
            except Exception:
                pass
            QTimer.singleShot(0, self._batch_next)
        else:
            set_batch_progress_note("当前条后中止")
        return True

    def _batch_current_record_url(self) -> str:
        """当前条写入记录文件时的原文。"""
        text = ""
        try:
            text = (self.url_edit.text() or "").strip()
        except Exception:
            text = ""
        if text:
            return text
        if getattr(self, "_batch_mode", False):
            urls = getattr(self, "_batch_urls", None) or []
            idx = int(getattr(self, "_batch_index", 0) or 0) - 1
            if 0 <= idx < len(urls):
                return (urls[idx] or "").strip()
        return ""

    def _batch_remove_current_record_line(self) -> bool:
        """本页批处理：校验成功后从记录文件删当前行。"""
        path = (getattr(self, "_batch_file_path", None) or "").strip()
        url = self._batch_current_record_url()
        if not path or not url:
            return False
        ok = _batch_remove_url_from_file(path, url)
        if ok:
            self._log(f"✓ 已从记录删除：{url[:60]}…", "ok")
        else:
            self._log(f"⚠ 记录删行未匹配：{url[:60]}…", "warn")
        return ok

    def _report_unified_batch_result(self, ok: bool = None, note: str = ""):
        """把本条结果上报给 PageVideo 自动下载引擎（校验通过才删行）。"""
        if ok is None:
            ok = bool(getattr(self, "_last_download_ok", False))
        note = (note or getattr(self, "_last_batch_note", None) or "").strip()
        if not note:
            note = "成功" if ok else "失败"
        eng = _find_unified_batch_engine(self)
        if eng is None:
            return False
        try:
            eng.notify_batch_item_done(
                bool(ok),
                note=note,
                source=getattr(self, "_BATCH_ENGINE_KEY", "") or "",
            )
            return True
        except Exception:
            return False

    def _batch_schedule_next(self, delay_ms: int = 2000):
        """预约下一条；若已 F6 中止则立刻收口。"""
        if getattr(self, "_batch_stop_requested", False):
            try:
                self._batch_timer.stop()
            except Exception:
                pass
            QTimer.singleShot(0, self._batch_next)
            return
        self._batch_timer.start(int(delay_ms))

    def _batch_next(self):
        if getattr(self, "_batch_stop_requested", False) or (
            not self._batch_mode or not self._batch_urls
            or self._batch_index >= len(self._batch_urls)
        ):
            cnt = self._batch_index
            ok = self._batch_ok
            err = self._batch_err
            skip = self._batch_skip
            path = self._batch_file_path
            stopped = bool(getattr(self, "_batch_stop_requested", False))
            self._batch_urls = []
            self._batch_index = 0
            self._batch_mode = False
            self._batch_file_path = ""
            self._batch_ok = 0
            self._batch_err = 0
            self._batch_skip = 0
            self._batch_stop_requested = False
            if hasattr(self, "btn_batch"):
                self.btn_batch.setEnabled(True)
            try:
                stop_batch_progress_toast()
            except Exception:
                pass
            # 批处理收口：强制隐藏简易进度线
            try:
                self._emit_nav_progress(False)
            except Exception:
                pass
            # 完成摘要不再写本页状态/日志（统一由速存图文侧报告承接）
            if cnt > 0:
                show_cursor_toast(
                    "批处理",
                    "已中止" if stopped else "完成",
                    current=cnt, total=cnt,
                    ok=ok, skip=skip, err=err,
                    accent="info" if stopped else "ok",
                )
            if cnt > 0 and not stopped and err == 0 and path and os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8-sig") as f:
                        remaining = [
                            ln for ln in f
                            if ln.strip() and not ln.strip().startswith("#")
                        ]
                    if not remaining:
                        os.remove(path)
                except Exception:
                    pass
            return
        url = self._batch_urls[self._batch_index].strip()
        self._batch_index += 1
        pct = int(self._batch_index / len(self._batch_urls) * 100)
        self._set_status(f"{url[:50]}…", self._progress_gradient_color(pct))
        show_batch_progress_toast(
            getattr(self, "_batch_platform_label", None) or self._BATCH_LABEL,
            self._batch_index,
            len(self._batch_urls),
        )
        self.url_edit.setText(url)
        self._start_flow(False)

    # ── 主流程 ───────────────────────────────────────────────────────────────

    def _delay_ms(self) -> int:
        if hasattr(self, "_gallery_delay_fn") and self._gallery_delay_fn:
            return self._gallery_delay_fn()
        return 1200

    # ── 主流程 ────────────────────────────────────────────────────────────────

    def _start_flow(self, use_clipboard=True):
        if getattr(self, "_is_placeholder", False):
            return
        text = ""
        if use_clipboard:
            raw = self._clipboard_text()
            try:
                if raw:
                    extract_url_from_text(raw)
                    text = raw
                    self.url_edit.setText(text)
                    self._log("已从剪贴板读取链接", "ok")
            except Exception:
                text = ""
        if not text:
            text = self.url_edit.text().strip()
        if not text:
            self._log("剪贴板与输入框都没有链接", "warn")
            self._set_status("没有链接", tk("warn"))
            return
        self._auto_dl = True
        self._parse(text)

    def _parse(self, url_text: str):
        if self._parse_worker and self._parse_worker.isRunning():
            self._log("正在解析中…", "warn")
            return
        if self._dl_worker and self._dl_worker.isRunning():
            self._log("正在下载中，请先等待或取消", "warn")
            return
        self._cancel[0] = False
        self.btn_sel_dl.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self._set_status("解析中…", tk("warn"))
        self._log("开始解析…")
        self._clear_name_cards()
        self._show_empty_cards()
        self._info = None
        self._last_failed = []
        self._emit_nav_progress(True, mode="busy")
        self._parse_worker = PixivParseWorker(
            url_text, self.ck_path.text().strip(), self._cancel
        )
        self._parse_worker.ok.connect(self._on_parse_ok)
        self._parse_worker.err.connect(self._on_parse_err)
        self._parse_worker.log.connect(self._log)
        self._parse_worker.finished.connect(self._on_parse_finished)
        self._parse_worker.start()

    def _on_parse_finished(self):
        if not (self._dl_worker and self._dl_worker.isRunning()):
            self.btn_cancel.setEnabled(False)
            self._update_action_buttons()

    def _work_title(self, fallback: str = "pixiv") -> str:
        ov = getattr(self, "_folder_title_override", None)
        if ov:
            return str(ov)
        return (self._info or {}).get("title") or fallback

    def _gallery_dir(self) -> str:
        ov = (getattr(self, "_gallery_dir_override", None) or "").strip()
        if ov:
            return ov
        save_dir = (self.save_edit.text() or "").strip()
        if not save_dir:
            save_dir = os.path.expanduser("~/Downloads")
        return gallery_folder_path(save_dir, self._work_title("pixiv"))

    def _save_root(self) -> str:
        save_dir = (self.save_edit.text() or "").strip()
        if not save_dir:
            save_dir = os.path.expanduser("~/Downloads")
        return save_dir

    def _clear_fill_state(self):
        self._fill_only_names = None
        self._fill_report_path = ""
        self._gallery_dir_override = ""
        self._bypass_record = False
        self._fill_actual_folder = ""

    def handoff_fill_missing(
        self,
        source_url: str,
        gallery_folder: str,
        missing_names,
        *,
        report_path: str = "",
    ) -> bool:
        """序列文件检查「补全」入口（Pixiv）。"""
        url = (source_url or "").strip()
        folder = os.path.abspath((gallery_folder or "").strip())
        if not url:
            self._log("补全失败：无源链接", "err")
            return False
        if not folder or not os.path.isdir(folder):
            self._log(f"补全失败：目录无效 {folder}", "err")
            return False
        names = {str(x).strip() for x in (missing_names or []) if str(x).strip()}
        if not names:
            self._log("补全：序列检查无缺失项，已跳过", "ok")
            return False
        if self._parse_worker and self._parse_worker.isRunning():
            self._log("补全失败：正在解析中", "warn")
            return False
        if self._dl_worker and self._dl_worker.isRunning():
            self._log("补全失败：正在下载中", "warn")
            return False
        self._fill_only_names = names
        self._fill_report_path = (report_path or "").strip() or os.path.join(
            folder, MISSING_REPORT_NAME
        )
        self._gallery_dir_override = folder
        self._bypass_record = True
        parent = os.path.dirname(folder)
        leaf = os.path.basename(folder.rstrip("\\/")) or "pixiv"
        if parent:
            try:
                self.save_edit.setText(parent.replace("\\", "/"))
            except Exception:
                pass
        self._folder_title_override = leaf
        try:
            self.url_edit.setText(url)
        except Exception:
            pass
        self._auto_dl = True
        self._log(
            f"序列补全：目录 {folder} · 待补 {len(names)} 项 · 源 {url[:80]}",
            "ok",
        )
        self._parse(url)
        return True

    def _apply_fill_card_selection(self) -> int:
        names = getattr(self, "_fill_only_names", None)
        if not names:
            return 0
        folder = self._gallery_dir()
        sel_n = 0
        matched = set()
        for card in getattr(self, "_name_cards", []) or []:
            cname = getattr(card, "_name", "") or ""
            if fill_name_matches(cname, names) and not image_file_exists(folder, cname):
                card.set_downloaded(False)
                card.set_selected(True)
                sel_n += 1
                matched.add(cname)
            else:
                if image_file_exists(folder, cname):
                    card.set_downloaded(True, keep_selected=False)
                else:
                    card.set_downloaded(False)
                    card.set_selected(False)
        unmatched = []
        for miss in names:
            any_card = any(
                fill_name_matches(getattr(c, "_name", ""), {miss})
                for c in (self._name_cards or [])
            )
            if not any_card:
                unmatched.append(miss)
        if unmatched:
            preview = "、".join(unmatched[:8])
            more = f" 等{len(unmatched)}个" if len(unmatched) > 8 else ""
            self._log(
                f"补全：解析结果中无对应图源卡（序列名）：{preview}{more}",
                "warn",
            )
        self._update_action_buttons()
        return sel_n

    def _maybe_delete_fill_report(self):
        """补全收口：按盘面同步 _缺失文件清单.txt（已齐删除 / 未齐重写）。"""
        report = (getattr(self, "_fill_report_path", None) or "").strip()
        names = getattr(self, "_fill_only_names", None)
        folder = (
            (getattr(self, "_fill_actual_folder", None) or "").strip()
            or (getattr(self, "_gallery_dir_override", None) or "").strip()
            or self._gallery_dir()
        )
        if not report and not names:
            return
        rp = report or (
            os.path.join(folder, MISSING_REPORT_NAME) if folder else ""
        )
        info = getattr(self, "_info", None) or {}
        meta = parse_missing_report(rp) if rp and os.path.isfile(rp) else {}
        expected = int(meta.get("expected_count") or 0) or len(
            info.get("images") or []
        )
        try:
            still_rows = collect_fill_still_rows(
                folder,
                names,
                report_path=rp,
                info=info,
                last_failed=getattr(self, "_last_failed", None),
                expected_count=expected,
            )
        except Exception as e:
            self._log(f"统计仍缺项失败：{e}", "warn")
            still_rows = []
        title = meta.get("title") or info.get("title") or ""
        source = meta.get("source_url") or info.get("source_url") or ""
        existed = bool(rp and os.path.isfile(rp))
        fail_n = len(getattr(self, "_last_failed", None) or [])
        if not still_rows and fail_n > 0:
            for it in getattr(self, "_last_failed", None) or []:
                nm = str((it or {}).get("name") or "").strip()
                url = str((it or {}).get("url") or "").strip()
                if nm:
                    still_rows.append((nm, url))
        try:
            dest = sync_missing_report(
                folder,
                still_rows,
                report_path=rp,
                title=title,
                source_url=source,
                expected_count=expected,
                fail_n=fail_n,
            )
        except Exception as e:
            self._log(f"同步缺失清单失败：{e}", "warn")
            return
        if not still_rows:
            if existed:
                self._log(f"已删除 {os.path.basename(rp or MISSING_REPORT_NAME)}", "ok")
            return
        self._log(
            f"补全未齐，已更新清单（仍缺 {len(still_rows)} 项）",
            "warn",
        )
        if dest:
            self._log(f"📄 缺失清单已更新：{os.path.basename(dest)}", "warn")

    def _resolve_same_name(
        self,
        images: list,
        *,
        dialog_title: str = "Pixiv 下载 · 发现同名文件",
        fallback_leaf: str = "pixiv",
    ):
        """同名处理：

        1) 有同名但未下全 → 不弹窗，继续下缺失
        2) 有同名且内容卡已全部在盘 → 弹窗 中止/改名/覆盖
        """
        images = list(images or [])
        save_dir = self._save_root()
        title = self._work_title(fallback_leaf)
        folder = gallery_folder_path(save_dir, title)
        self._dup_compare_dir = ""

        total = len(images)
        if total <= 0:
            return {"title": title, "skip_existing": None}

        done_n = 0
        for it in images:
            name = str(it.get("name") or f"{int(it.get('index') or 0):04d}")
            if image_file_exists(folder, name):
                done_n += 1

        if done_n <= 0:
            return {"title": title, "skip_existing": None}

        if done_n < total:
            self._log(
                f"目录已有 {done_n}/{total} 张，未下全 — 继续下载缺失项（不弹窗）",
                "ok",
            )
            return {"title": title, "skip_existing": True}

        try:
            from utils.download_confirm import next_numbered_dir_title
        except Exception as e:
            self._log(f"同名处理模块加载失败：{e}", "warn")
            return {"title": title, "skip_existing": None}

        # Pixiv 与抖音相同：目录已齐也不弹窗。另存到 leaf_N，下完再比各文件大小。
        # 序列补全必须写回原目录，不走这套。
        if getattr(self, "_fill_only_names", None):
            return {"title": title, "skip_existing": True}

        old_leaf = _safe_folder_name(title, fallback_leaf)
        new_leaf = next_numbered_dir_title(save_dir, old_leaf)
        self._folder_title_override = new_leaf
        self._dup_compare_dir = os.path.join(save_dir, old_leaf)
        self._log(
            f"目录已齐 {done_n}/{total} 张：将另存为「{new_leaf}」，"
            "完成后按文件大小判定是否真重复",
            "blue",
        )
        return {"title": new_leaf, "skip_existing": True}

    def _mark_all_cards_for_redownload(self):
        for card in getattr(self, "_name_cards", []) or []:
            try:
                card.set_downloaded(False)
                card.set_selected(True)
            except Exception:
                pass
        self._update_action_buttons()

    def _open_save_dir(self):
        """打开图集保存目录（有解析结果则进作品子目录，否则进根保存路径）。"""
        path = ""
        try:
            if getattr(self, "_info", None):
                path = (self._gallery_dir() or "").strip()
        except Exception:
            path = ""
        if not path:
            try:
                path = (self.save_edit.text() or "").strip()
            except Exception:
                path = ""
        if not path:
            path = os.path.expanduser("~/Downloads")
        try:
            if not os.path.isdir(path):
                root = (self.save_edit.text() or "").strip() or os.path.expanduser("~/Downloads")
                if os.path.isdir(root):
                    path = root
                else:
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

    def _selected_images(self) -> list:
        out = []
        for card in getattr(self, "_name_cards", []) or []:
            if not card.is_selected():
                continue
            it = dict(card.item or {})
            it["name"] = card._name
            out.append(it)
        return out

    def _set_record_section_visible(self, visible: bool):
        """显示/隐藏页底「已下载记录」整行。"""
        bar = getattr(self, "_record_bar", None)
        if bar is not None:
            try:
                bar.setVisible(bool(visible))
                return
            except Exception:
                pass
        for attr in ("lbl_record_count", "btn_view_file", "btn_update_list"):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    w.setVisible(bool(visible))
                except Exception:
                    pass

    def _refresh_record_count(self, added_names=None):
        _gallery_apply_record_hint(self, added_names)

    def _on_view_file(self):
        _open_gallery_records_dialog(self)

    def _on_update_list(self):
        # Pixiv 无下载记录；hitomi 子类覆盖为扫描 zip
        pass

    def _missing_list(self) -> list:
        if not self._info:
            return []
        return list_missing_images(self._info.get("images") or [], self._gallery_dir())

    def _update_action_buttons(self):
        has = bool(self._info and self._info.get("images"))
        busy = bool(
            (self._dl_worker and self._dl_worker.isRunning())
            or (self._parse_worker and self._parse_worker.isRunning())
        )
        n_sel = len(self._selected_images()) if has else 0
        self.btn_sel_dl.setEnabled(has and not busy and n_sel > 0)

    def _on_parse_ok(self, info: dict):
        self._info = info
        self._last_failed = []
        if not getattr(self, "_fill_only_names", None):
            self._folder_title_override = None
        self._conflict_decision = None
        imgs = info.get("images") or []
        title = info.get("title") or ""
        site = info.get("site") or ""
        self._fill_name_cards(imgs)
        self._update_action_buttons()
        self._set_status(f"√ {site} · {title[:40]}{'…' if len(title) > 40 else ''} · 共 {len(imgs)} 张", tk("ok"))

        if getattr(self, "_fill_only_names", None):
            sel_n = self._apply_fill_card_selection()
            self._conflict_decision = {
                "title": self._work_title("pixiv"),
                "skip_existing": True,
            }
            self._log(f"序列补全：将下载 {sel_n} 张真实缺失", "ok")
            if self._auto_dl:
                self._auto_dl = False
                if sel_n <= 0:
                    self._log("序列补全：无可下载项（可能已齐或未匹配到图源）", "warn")
                    self._emit_nav_progress(False)
                    self._maybe_delete_fill_report()
                    self._clear_fill_state()
                    return
                QTimer.singleShot(0, self._download_selected)
            else:
                self._emit_nav_progress(False)
            return

        # 解析后：未下全静默续下；已下全才弹窗
        resolved = self._resolve_same_name(
            imgs,
            dialog_title="Pixiv 下载 · 发现同名文件",
            fallback_leaf="pixiv",
        )
        if resolved is None:
            self._auto_dl = False
            self._last_download_ok = False
            self._emit_nav_progress(False)
            return
        if resolved.get("skip_existing") is not None:
            self._conflict_decision = dict(resolved)
        if resolved.get("skip_existing") is True and getattr(
            self, "_folder_title_override", None
        ):
            self._fill_name_cards(imgs)
            self._update_action_buttons()
        elif resolved.get("skip_existing") is False:
            self._mark_all_cards_for_redownload()

        if self._auto_dl:
            self._auto_dl = False
            self._log("自动开始下载亮起的图源…", "ok")
            QTimer.singleShot(0, self._download_selected)
        else:
            self._emit_nav_progress(False)

    def _on_parse_err(self, msg: str):
        if getattr(self, "_skip_parse_err", False):
            self._skip_parse_err = False
            return
        self._auto_dl = False
        self._last_download_ok = False
        self._last_failed = []
        self.btn_sel_dl.setEnabled(False)
        self._set_status("解析失败 ✗", tk("err"))
        self._log("━" * 36, "err")
        for line in (msg or "").split("\n"):
            if line.strip():
                self._log(f"  {line.strip()}", "err")
        self._emit_nav_progress(False)
        if getattr(self, "_fill_only_names", None) is not None:
            self._clear_fill_state()
        self._log("━" * 36, "err")

    def _toggle_hide_done(self):
        hide = self.chk_hide_done.isChecked()
        for c in (self._name_cards or []):
            if getattr(c, "_downloaded", False):
                c.setVisible(not hide)
        self._relayout_name_cards()

    def _sync_card_selection_ui(self):
        """灰卡点选后刷新「已下/选中」统计与「选中下载」可用性。"""
        cards = getattr(self, "_name_cards", None) or []
        if not cards:
            self._set_status("")
            self._update_action_buttons()
            return
        done_n = sum(1 for c in cards if getattr(c, "_downloaded", False))
        sel_n = sum(1 for c in cards if c.is_selected())
        self._set_status(
            f"已下 {done_n}/{len(cards)} · 选中 {sel_n}",
            self._done_ratio_color(done_n, len(cards)),
        )
        self._update_action_buttons()

    def _download_selected(self):
        if not self._info or not (self._info.get("images") or []):
            self._log("请先成功解析作品", "warn")
            self._last_download_ok = False
            self._emit_nav_progress(False)
            return
        jobs = self._selected_images()
        if not jobs:
            all_imgs = list((self._info or {}).get("images") or [])
            resolved = self._resolve_same_name(
                all_imgs,
                dialog_title="Pixiv 下载 · 发现同名文件",
                fallback_leaf="pixiv",
            )
            if resolved is None:
                self._last_download_ok = False
                self._emit_nav_progress(False)
                return
            if resolved.get("skip_existing") is None:
                self._log("没有亮起的内容卡 — 全部已下载，跳过", "ok")
                self._last_download_ok = True
                self._last_batch_note = "成功"
                self._emit_nav_progress(False)
                if self._batch_mode:
                    self._batch_ok += 1
                    self._batch_remove_current_record_line()
                    self._batch_schedule_next(2000)
                else:
                    self._report_unified_batch_result(True, note="成功")
                return
            if resolved.get("skip_existing") is True:
                self._fill_name_cards(all_imgs)
            else:
                self._mark_all_cards_for_redownload()
            jobs = self._selected_images() or all_imgs
            if not jobs:
                self._log("没有可下载的项目", "warn")
                self._last_download_ok = False
                self._emit_nav_progress(False)
                if not self._batch_mode:
                    self._report_unified_batch_result(False, note="失败")
                return
            self._conflict_decision = dict(resolved)
            self._log(f"选中下载：{len(jobs)} 张（同名处理后）", "info")
            self._start_download(
                images=jobs,
                skip_existing=bool(resolved.get("skip_existing")),
                mode="download",
                retries=3,
            )
            return
        any_redown = any(
            getattr(c, "_downloaded", False) and c.is_selected()
            for c in (self._name_cards or [])
        )
        names = ", ".join((it.get("name") or "?") for it in jobs[:16])
        more = f" …共 {len(jobs)} 张" if len(jobs) > 16 else ""
        self._log(f"选中下载：{len(jobs)} 张（{names}{more}）", "info")
        self._start_download(
            images=jobs,
            skip_existing=not any_redown,
            mode="download",
            retries=3,
        )

    def _collect_gallery_conflicts(
        self, images: list, save_dir: str, *, title_override: str = None
    ) -> list:
        title = title_override or self._work_title("pixiv")
        folder = gallery_folder_path(save_dir, title)
        if not os.path.isdir(folder):
            return []
        paths = []
        for it in images or []:
            name = str(it.get("name") or f"{int(it.get('index') or 0):04d}")
            for e in ("webp", "jpg", "jpeg", "png", "gif", "avif", "bmp"):
                p = os.path.join(folder, f"{name}.{e}")
                try:
                    if os.path.isfile(p) and os.path.getsize(p) > 256:
                        paths.append(p)
                except Exception:
                    log.debug("检查已存在文件失败 path=%s", p, exc_info=True)
        return paths

    def _start_download(self, images: list, *, skip_existing: bool = True, mode: str = "download", retries: int = 3):
        if self._dl_worker and self._dl_worker.isRunning():
            return
        if self._parse_worker and self._parse_worker.isRunning():
            self._log("解析尚未结束", "warn")
            return
        if not images:
            self._log("没有可下载的项目", "warn")
            return
        save_dir = self._save_root()
        if not (self.save_edit.text() or "").strip():
            self.save_edit.setText(save_dir.replace("\\", "/"))
        title = self._work_title("pixiv")
        pending = getattr(self, "_conflict_decision", None)
        self._conflict_decision = None
        if isinstance(pending, dict) and pending.get("skip_existing") is not None:
            title = pending.get("title") or title
            skip_existing = bool(pending.get("skip_existing"))
        else:
            check_imgs = list(images or [])
            for it in (self._info or {}).get("images") or []:
                if it not in check_imgs:
                    check_imgs.append(it)
            resolved = self._resolve_same_name(
                check_imgs,
                dialog_title="Pixiv 下载 · 发现同名文件",
                fallback_leaf="pixiv",
            )
            if resolved is None:
                self._emit_nav_progress(False)
                return
            title = resolved.get("title") or title
            if resolved.get("skip_existing") is not None:
                skip_existing = bool(resolved["skip_existing"])
            if resolved.get("skip_existing") is False:
                self._mark_all_cards_for_redownload()
                images = self._selected_images() or list(
                    (self._info or {}).get("images") or images
                )
            elif resolved.get("skip_existing") is True and getattr(
                self, "_folder_title_override", None
            ):
                self._fill_name_cards((self._info or {}).get("images") or images)
                images = self._selected_images() or list(
                    (self._info or {}).get("images") or images
                )
        # 尾巴功能暂闭
        # self._tail_url_text = self.url_edit.text().strip()
        # self._tail_expected_count = len((self._info or {}).get("images") or images or [])
        self._cancel[0] = False
        self._dl_summary_set = False
        self.btn_sel_dl.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self._set_status("准备下载…", self._progress_gradient_color(0))
        fill_mode = bool(getattr(self, "_fill_only_names", None))
        if fill_mode:
            mode = "fill"
            skip_existing = True
        folder_override = ""
        if fill_mode:
            folder_override = (getattr(self, "_gallery_dir_override", None) or "").strip()
        self._emit_nav_progress(True, 0, mode="progress")
        self._dl_worker = GalleryDownloadWorker(
            images=images,
            save_dir=save_dir,
            title=title,
            cookie_path=self.ck_path.text().strip(),
            delay_ms=self._delay_ms(),
            skip_existing=skip_existing,
            cancel_flag=self._cancel,
            retries=retries,
            refresh_url=True,
            gallery_url=(self._info or {}).get("source_url") or "",
            mode=mode,
            folder_override=folder_override,
        )
        self._dl_worker.progress.connect(self._on_dl_progress)
        self._dl_worker.ok.connect(self._on_dl_ok)
        self._dl_worker.err.connect(self._on_dl_err)
        self._dl_worker.log.connect(self._log)
        self._dl_worker.done_detail.connect(self._on_dl_detail)
        self._dl_worker.item_done.connect(self._on_item_done)
        self._dl_worker.finished_clean.connect(self._on_dl_finished)
        self._dl_worker.start()

    def _on_dl_progress(self, pct: int, text: str):
        try:
            pct = max(0, min(100, int(pct)))
        except (TypeError, ValueError):
            pct = 0
        self._on_progress_value(pct)
        body = (text or "下载中…").strip()
        if not str(body).startswith(f"{pct}%"):
            body = f"{pct}% · {body}"
        self._set_status(body, self._progress_gradient_color(pct))
        self._emit_nav_progress(True, pct, mode="progress")

    def _maybe_dedupe_renamed_dir(self, folder: str) -> str:
        """另存目录下完后按各文件大小比对；真重复则删新目录。"""
        can = getattr(self, "_dup_compare_dir", "") or ""
        self._dup_compare_dir = ""
        if not can or not folder or not os.path.isdir(folder):
            return folder
        try:
            from utils.download_confirm import dedupe_dir_by_size_after_rename
            kept = dedupe_dir_by_size_after_rename(can, folder, log_emit=self._log)
        except Exception:
            log.exception("Pixiv 同名目录比对失败")
            return folder
        try:
            same = os.path.normcase(os.path.abspath(kept or "")) == os.path.normcase(
                os.path.abspath(folder)
            )
        except Exception:
            same = kept == folder
        if not same:
            self._folder_title_override = os.path.basename(kept.rstrip("\\/"))
            self._last_download_ok = True
            self._last_batch_note = "已下载过"
        return kept

    def _on_dl_ok(self, folder: str):
        folder = self._maybe_dedupe_renamed_dir(folder)
        # 校验以 _on_dl_detail 为准；此处兜底（detail 未写结算标志时仍视为成功）
        if not getattr(self, "_dl_summary_set", False):
            self._last_download_ok = True
            self._last_batch_note = "成功"
        elif not (getattr(self, "_last_batch_note", None) or "").strip():
            # detail 只写了 UI、漏写 _last_batch_note 时，避免自动下载误记「败」
            self._last_download_ok = True
            self._last_batch_note = "成功"
        self._on_progress_value(100)
        # 状态行由 _on_dl_detail 写最终总结（ok 信号晚于 detail，勿覆盖）
        if not getattr(self, "_dl_summary_set", False):
            self._set_status("完成：已保存", tk("ok"))
        self._log(f"✓ 已保存到：{folder}", "ok")
        self._emit_nav_progress(True, 100, mode="progress")
        if not in_batch_download(self):
            show_cursor_toast("下载", "完成", accent="ok")
        elif self._batch_mode:
            note = "成功" if getattr(self, "_last_download_ok", False) else "失败"
            total = len(self._batch_urls or [])
            if total:
                show_batch_progress_toast(
                    getattr(self, "_batch_platform_label", None) or self._BATCH_LABEL,
                    self._batch_index,
                    total,
                    note=note,
                )
        else:
            note = (
                (getattr(self, "_last_batch_note", None) or "").strip()
                or ("成功" if getattr(self, "_last_download_ok", False) else "失败")
            )
            set_batch_progress_note(note)

    def _on_dl_err(self, msg: str):
        self._last_download_ok = False
        self._last_batch_note = "失败"
        self._dl_summary_set = False
        self._set_status("失败 / 取消", tk("err"))
        self._log(f"✗ {msg}", "err")
        self._emit_nav_progress(False)
        if self._batch_mode:
            self._batch_err += 1
            self._batch_schedule_next(2000)
            total = len(self._batch_urls or [])
            if total:
                show_batch_progress_toast(
                    getattr(self, "_batch_platform_label", None) or self._BATCH_LABEL,
                    self._batch_index,
                    total,
                    note="失败",
                )
        else:
            if not self._report_unified_batch_result(False, note="失败"):
                if in_batch_download(self):
                    set_batch_progress_note("失败")

    def _on_item_done(self, name: str):
        card = (self._card_by_name or {}).get(name)
        if card is not None:
            card.set_downloaded(True, keep_selected=False)
        if self._card_by_name:
            done_n = sum(
                1 for c in self._card_by_name.values()
                if getattr(c, "_downloaded", False)
            )
            total = len(self._card_by_name)
            pct = int(done_n * 100 / max(1, total))
            self._set_status(
                f"{pct}% · 已下 {done_n}/{total}",
                self._progress_gradient_color(pct),
            )

    def _on_dl_detail(self, detail: dict):
        """记录失败项，并把刷新后的 url 写回 _info，方便下次补全。"""
        failed = list(detail.get("failed") or [])
        self._last_failed = failed
        if self._info and self._info.get("images"):
            by_name = {
                (it.get("name") or ""): it
                for it in self._info["images"]
            }
            for it in failed:
                nm = it.get("name") or ""
                if nm and nm in by_name and it.get("url"):
                    by_name[nm]["url"] = it["url"]
        failed_names = [
            (it.get("name") or "") for it in failed if it.get("name")
        ]
        self._refresh_card_states(failed_names=failed_names)
        miss = self._missing_list()
        ok_n = int(detail.get("ok_n") or 0)
        skip_n = int(detail.get("skip_n") or 0)
        fail_n = int(detail.get("fail_n") or 0)
        miss_n = len(miss)
        # zip 打包（hitomi）不落单图文件，不能用 miss_n 判定
        is_zip = bool(detail.get("zip_path")) or (
            getattr(self, "_BATCH_ENGINE_KEY", "") == "hitomi"
        )
        if is_zip:
            validated = (fail_n == 0 and (ok_n + skip_n) > 0)
        else:
            validated = (fail_n == 0 and miss_n == 0 and (ok_n + skip_n) > 0)
        self._last_download_ok = validated
        self._last_batch_note = "成功" if validated else "失败"
        if fail_n:
            summary = f"完成：成功 {ok_n} · 失败 {fail_n} · 仍缺 {miss_n} 张"
            color = tk("warn")
        elif (not is_zip) and miss_n:
            if skip_n:
                summary = f"完成：成功 {ok_n}（跳过 {skip_n}）· 仍缺 {miss_n} 张"
            else:
                summary = f"完成：成功 {ok_n} · 仍缺 {miss_n} 张"
            color = tk("warn")
        elif skip_n:
            summary = f"完成：成功 {ok_n} 张（跳过 {skip_n}）· 已齐全"
            color = tk("ok")
        else:
            summary = f"完成：成功 {ok_n} 张 · 已齐全"
            color = tk("ok")
        self._set_status(summary, color)
        self._dl_summary_set = True

        fill_mode = getattr(self, "_fill_only_names", None) is not None
        # 定点补全：清单删/改交给 _maybe_delete_fill_report，避免用本次子集 images 覆盖整本
        if (not is_zip) and (not fill_mode) and miss and self._info:
            folder = self._gallery_dir()
            rp = write_missing_report(self._info, folder, ok_n=ok_n)
            if rp:
                self._log(f"📄 缺失清单已保存：{os.path.basename(rp)}", "warn")
        elif (not is_zip) and (not fill_mode):
            # 全部齐全：清除旧报告
            old_rp = os.path.join(self._gallery_dir(), MISSING_REPORT_NAME)
            if os.path.isfile(old_rp):
                try:
                    os.remove(old_rp)
                except Exception:
                    pass

        if self._batch_mode:
            if validated:
                self._batch_ok += 1
                self._batch_remove_current_record_line()
            else:
                self._batch_err += 1
            self._batch_schedule_next(3000)

    def _on_dl_finished(self):
        self.btn_cancel.setEnabled(False)
        self._refresh_card_states(
            failed_names=[
                (it.get("name") or "") for it in (self._last_failed or [])
            ]
        )
        self._update_action_buttons()
        if getattr(self, "_fill_only_names", None) is not None:
            try:
                self._maybe_delete_fill_report()
            except Exception:
                pass
            self._clear_fill_state()
        note = (getattr(self, "_last_batch_note", None) or "").strip()
        if getattr(self, "_last_download_ok", False) or note in ("成功", "已下载过"):
            _gallery_commit_download_record(self)
        self._emit_nav_progress(False)
        if not getattr(self, "_batch_mode", False):
            self._report_unified_batch_result()

    def _clear_url_after_download_tail(self):
        images = list((self._info or {}).get("images") or [])
        expected = int(getattr(self, "_tail_expected_count", 0) or len(images))
        if expected <= 0 or not images:
            return
        folder = self._gallery_dir()
        done_n = 0
        for it in images:
            name = str(it.get("name") or f"{int(it.get('index') or 0):04d}")
            if image_file_exists(folder, name):
                done_n += 1
        if done_n >= expected and self.url_edit.text().strip() == getattr(self, "_tail_url_text", ""):
            self.url_edit.clear()
            self._clear_name_cards()
            self._tail_url_text = ""
            self._tail_expected_count = 0
            self._log(
                f"下载尾巴：已确认目录图片齐全（{done_n}/{expected}），清空当前链接与图源列表，避免重复自动下载。",
                "ok",
            )

    # ── 内容卡 ────────────────────────────────────────────────────────────────

    def _clipboard_text(self) -> str:
        cb = QApplication.clipboard()
        if cb is None:
            return ""
        return (cb.text() or "").strip()

    def _show_empty_cards(self):
        self._clear_name_cards()
        self._set_status("粘贴链接 → 回车自动解析")

    def _clear_name_cards(self):
        for c in list(getattr(self, "_name_cards", []) or []):
            try:
                if getattr(self, "_card_layout", None) is not None:
                    self._card_layout.removeWidget(c)
                c.setParent(None)
                c.deleteLater()
            except Exception:
                pass
        self._name_cards = []
        self._card_by_name = {}
        self._relayout_name_cards()

    def _fill_name_cards(self, images: list):
        self._clear_name_cards()
        folder = self._gallery_dir()
        done_n = 0
        sel_n = 0
        for it in images or []:
            card = GalleryNameCard(it)
            name = card._name
            if image_file_exists(folder, name):
                card.set_downloaded(True, keep_selected=False)
                done_n += 1
            else:
                card.set_downloaded(False)
                card.set_selected(True)
                sel_n += 1
            self._name_cards.append(card)
            self._card_by_name[name] = card
            self._card_layout.addWidget(card)
        total = len(self._name_cards)
        if total:
            self._set_status(
                f"已下 {done_n}/{total}",
                self._done_ratio_color(done_n, total),
            )
        else:
            self._set_status("")
        self._toggle_hide_done()  # 内部会 _relayout_name_cards

    def _refresh_card_states(self, failed_names=None):
        """按磁盘状态刷新。

        · 已在磁盘：灰卡；若用户已点亮则保留（准备重下）
        · 未在磁盘（含失败）：始终亮起，不可点灭
        """
        failed_names = set(failed_names or [])
        folder = self._gallery_dir()
        done_n = 0
        for name, card in (self._card_by_name or {}).items():
            was_sel = card.is_selected()
            if image_file_exists(folder, name):
                card.set_downloaded(True, keep_selected=was_sel)
                done_n += 1
            elif name in failed_names:
                card.set_failed(True)
            else:
                card.set_downloaded(False)
        total = len(self._card_by_name)
        if total:
            self._set_status(
                f"已下 {done_n}/{total}",
                self._done_ratio_color(done_n, total),
            )

    def shutdown(self):
        from utils.qthread_util import stop_qthreads
        stop_qthreads(
            (getattr(self, "_parse_worker", None), getattr(self, "_dl_worker", None)),
            cancel_flag=getattr(self, "_cancel", None),
        )
        self._emit_nav_progress(False)

    def freeze_as_placeholder(self):
        self._is_placeholder = True
        self._cookie_auto_tried = True
        self._auto_dl = False
        self.setEnabled(False)

    # ── 用户习惯 ──────────────────────────────────────────────────────────────

    def export_settings(self) -> dict:
        return {
            "save_path": (self.save_edit.text() or "").strip(),
            "cookie_path": (self.ck_path.text() or "").strip(),
        }

    def apply_settings(self, d: dict):
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
            self._on_cookie_change(ck)
            self._cookie_auto_tried = True
            self._cookie_loaded_via = "manual"


class _GalleryHitomiPage(_GalleryPixivPage):
    """hitomi.la：无需 Cookie；对齐官网 Download 按钮，下载为单个 .zip。"""

    _BATCH_LABEL = "hitomi.la"
    _BATCH_ENGINE_KEY = "hitomi"

    def __init__(self):
        super().__init__()
        self._batch_platform_label = self._BATCH_LABEL
        # hitomi 使用 zip 名防重复；恢复父类里对 pixiv 隐藏的记录版块
        self._record_key = "gallery_hitomi"
        self._set_record_section_visible(True)
        self._refresh_record_count()
        # 跳过 Cookie 自动扫描
        self._cookie_auto_tried = True
        self._cookie_loaded_via = None
        self._debug_worker = None
        try:
            self.url_edit.setPlaceholderText(
                "粘贴 hitomi.la 图库链接 → 回车解析并打包 zip（无需 Cookie）"
            )
            self._set_status("粘贴 hitomi.la 链接 → 解析并下载 zip（无需 Cookie）")
        except Exception:
            pass
        # 「调试」按钮保留方法，界面隐藏
        self.btn_debug = None
        try:
            self.log_box.clear()
        except Exception:
            pass
        self._log(
            "hitomi.la：无需 Cookie · 输出单个 .zip（官网 Download 同款：本地打包 webp）",
            "ok",
        )
        self._log(
            "说明：官网并无服务端整包 zip，点 Download 也是浏览器内逐张拉图再 JSZip。"
            "完整取证：python tools/hitomi_zip_debug.py <链接>",
            "info",
        )

    def showEvent(self, event):
        # 不调用 Pixiv 的 Cookie 自动加载
        QWidget.showEvent(self, event)

    def _try_autoload_cookie(self):
        return

    def _auto_load_cookie(self):
        return

    def _run_debug(self):
        """后台检测当前输入框 / 剪贴板链接的直链是否正确。"""
        if self._debug_worker and self._debug_worker.isRunning():
            self._log("调试进行中…", "warn")
            return
        text = (self.url_edit.text() or "").strip()
        if not text:
            try:
                text = self._clipboard_text()
            except Exception:
                text = ""
        if not text:
            self._log("请先粘贴 hitomi 链接再点调试", "warn")
            return
        try:
            extract_hitomi_id(text)
        except Exception as e:
            self._log(f"不是有效 hitomi 链接：{e}", "err")
            return
        self._log("开始调试 hitomi 直链…", "info")
        if self.btn_debug is not None:
            self.btn_debug.setEnabled(False)

        w = HitomiDebugWorker(text, sample=8, parent=self)

        def _done(rep):
            if self.btn_debug is not None:
                self.btn_debug.setEnabled(True)
            if not isinstance(rep, dict):
                self._log("调试无结果", "err")
                return
            if rep.get("error") and not rep.get("sample"):
                self._log(f"调试失败：{rep.get('error')}", "err")
            else:
                ok_n = rep.get("ok_n", 0)
                fail_n = rep.get("fail_n", 0)
                gg = rep.get("gg") or {}
                self._log(
                    f"调试结果：成功 {ok_n} · 失败 {fail_n} · "
                    f"m=({gg.get('default_m')}/{gg.get('case_m')}) · b={gg.get('b')}",
                    "ok" if rep.get("ok") else "warn",
                )
                if rep.get("zip_name"):
                    self._log(f"预期 zip 名：{rep.get('zip_name')}", "info")
                if rep.get("report_path"):
                    self._log(f"完整报告：{rep.get('report_path')}", "ok")
            w.deleteLater()
            self._debug_worker = None

        w.done.connect(_done)
        self._debug_worker = w
        w.start()

    def _hitomi_save_dir(self) -> str:
        save_dir = (self.save_edit.text() or "").strip()
        if not save_dir:
            save_dir = os.path.join(
                os.path.expanduser("~/Downloads"), "hitomi.la"
            ).replace("\\", "/")
            try:
                self.save_edit.setText(save_dir)
            except Exception:
                pass
        return save_dir.replace("\\", "/")

    def _gallery_dir(self) -> str:
        # zip 直接落在公共 hitomi.la 目录，不再套一层图集子文件夹
        return self._hitomi_save_dir()

    def _hitomi_zip_path(self) -> str:
        title = (self._info or {}).get("title") or "hitomi"
        return os.path.join(
            self._hitomi_save_dir(),
            f"{_safe_folder_name(title, 'hitomi')}.zip",
        ).replace("\\", "/")

    def _start_flow(self, use_clipboard=True):
        if getattr(self, "_is_placeholder", False):
            return
        text = ""
        if use_clipboard:
            raw = self._clipboard_text()
            try:
                if raw:
                    extract_hitomi_id(raw)
                    text = raw
                    self.url_edit.setText(text)
                    self._log("已从剪贴板读取链接", "ok")
            except Exception:
                text = ""
        if not text:
            text = self.url_edit.text().strip()
        if not text:
            self._log("剪贴板与输入框都没有链接", "warn")
            self._set_status("没有链接", tk("warn"))
            return
        self._auto_dl = True
        self._parse(text)

    def _parse(self, url_text: str):
        if self._parse_worker and self._parse_worker.isRunning():
            self._log("正在解析中…", "warn")
            return
        if self._dl_worker and self._dl_worker.isRunning():
            self._log("正在下载中，请先等待或取消", "warn")
            return
        self._cancel[0] = False
        self._last_download_ok = False
        self._skip_parse_err = False
        self.btn_sel_dl.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self._set_status("解析中…", tk("warn"))
        self._log("开始解析 hitomi.la（官网 Download 同款）…")
        self._clear_name_cards()
        self._show_empty_cards()
        self._info = None
        self._last_failed = []
        self._emit_nav_progress(True, mode="busy")
        self._parse_worker = HitomiParseWorker(url_text, self._cancel)
        self._parse_worker.ok.connect(self._on_parse_ok)
        self._parse_worker.err.connect(self._on_parse_err)
        self._parse_worker.log.connect(self._log)
        self._parse_worker.finished.connect(self._on_parse_finished)
        self._parse_worker.title_ready.connect(self._on_title_ready)
        self._parse_worker.start()

    def _batch_toast_already(self) -> bool:
        """批处理中：把「已下载过」写到常驻气泡第一行末尾。"""
        if getattr(self, "_batch_mode", False) and getattr(self, "_batch_urls", None):
            total = len(self._batch_urls or [])
            if total > 0:
                show_batch_progress_toast(
                    getattr(self, "_batch_platform_label", None)
                    or getattr(self, "_BATCH_LABEL", "") or "",
                    int(getattr(self, "_batch_index", 0) or 0),
                    total,
                    note="已下载过",
                )
                return True
        # 自动下载：图集不在 PageVideo 父链上，直接改常驻气泡状态词
        if set_batch_progress_note("已下载过"):
            return True
        return False

    def _mark_already_downloaded(self, label: str) -> None:
        """命中防重复记录（hitomi：zip 名）。自动下载记成功，批处理计 skip。"""
        self._log("━" * 36, "info")
        self._log(f"📋 已下载记录：{label}", "info")
        self._log("该图集已在下载记录中，跳过重复下载", "ok")
        self._log("━" * 36, "info")
        try:
            self.lbl_hint.setText(f"📋 已下过 · {str(label)[:40]}")
            self.lbl_hint.setStyleSheet(
                "color:%s; font-weight:600; font-size:12px;" % tk("ok")
            )
        except Exception:
            pass
        if not self._batch_toast_already():
            show_cursor_toast("下载", "已下载过", accent="ok")
        self._last_download_ok = True
        self._last_batch_note = "已下载过"
        self._auto_dl = False
        self._set_status("已跳过 · 下载记录中已有", tk("ok"))
        if self._batch_mode:
            self._batch_skip += 1
            self._batch_ok += 1
            self._batch_remove_current_record_line()
            self._batch_schedule_next(1000)
        else:
            self._emit_nav_progress(False)
            self._report_unified_batch_result(True, note="已下载过")

    def _on_title_ready(self, title: str):
        zip_name = f"{_safe_folder_name(title, 'hitomi')}.zip"
        if _should_skip_by_record(self, zip_name, note_continue=False):
            self._cancel[0] = True
            self._skip_parse_err = True
            self._mark_already_downloaded(zip_name)

    def _on_parse_ok(self, info: dict):
        """覆盖 Pixiv 父类：补 hitomi 记录二次校验，再走自动下载流程。"""
        self._info = info
        self._last_failed = []
        self._folder_title_override = None
        title = (info or {}).get("title") or "hitomi"
        zip_name = f"{_safe_folder_name(title, 'hitomi')}.zip"
        if _should_skip_by_record(self, zip_name):
            self._mark_already_downloaded(zip_name)
            return
        # 未命中记录：沿用父类解析成功后的卡片/自动下载逻辑
        super()._on_parse_ok(info)

    def _collect_gallery_conflicts(self, images: list, save_dir: str) -> list:
        """已存在同名 .zip 时提示覆盖。"""
        zip_path = self._hitomi_zip_path()
        try:
            if os.path.isfile(zip_path) and os.path.getsize(zip_path) > 256:
                return [zip_path]
        except Exception:
            pass
        return []

    def _on_dl_ok(self, folder: str):
        zip_path = folder if str(folder or "").lower().endswith(".zip") else ""
        if not zip_path:
            try:
                zip_path = getattr(self, "_last_zip_path", "") or ""
            except Exception:
                zip_path = ""
        can = getattr(self, "_dup_compare_zip", "") or ""
        self._dup_compare_zip = ""
        kept = zip_path
        if can and zip_path:
            try:
                from utils.download_confirm import dedupe_by_size_after_rename
                kept = dedupe_by_size_after_rename(can, zip_path, log_emit=self._log)
            except Exception:
                log.exception("hitomi 同名 zip 比对失败")
                kept = zip_path
            try:
                same = os.path.normcase(os.path.abspath(kept or "")) == os.path.normcase(
                    os.path.abspath(zip_path)
                )
            except Exception:
                same = kept == zip_path
            if not same:
                self._last_download_ok = True
                self._last_batch_note = "已下载过"
        super()._on_dl_ok(folder)
        _gallery_commit_download_record(self)

    def _on_update_list(self):
        start = self._hitomi_save_dir()
        folder = QFileDialog.getExistingDirectory(self, "选择要扫描的文件夹", start)
        if not folder:
            return
        added = scan_dir_records("gallery_hitomi", folder, mode="zip")
        names = sorted(added) if added else []
        if names:
            self._log(f"已下载：新增 {len(names)} 条记录", "ok")
        else:
            self._log("已下载：未发现新记录", "ok")
        self._refresh_record_count(names)

    def _zip_exists(self) -> bool:
        try:
            p = self._hitomi_zip_path()
            return bool(p and os.path.isfile(p) and os.path.getsize(p) > 256)
        except Exception:
            return False

    def _missing_list(self) -> list:
        # 整包 zip 模型：有 zip 即视为齐全
        if self._zip_exists():
            return []
        return list((self._info or {}).get("images") or [])

    def _refresh_card_states(self, failed_names=None):
        """zip 已存在或下载中 item_done 标记 → 灰卡。"""
        failed_names = set(failed_names or [])
        zip_ok = self._zip_exists()
        done_n = 0
        for name, card in (self._card_by_name or {}).items():
            was_sel = card.is_selected()
            if zip_ok or getattr(card, "_downloaded", False):
                card.set_downloaded(True, keep_selected=was_sel and not zip_ok)
                done_n += 1
            elif name in failed_names:
                card.set_failed(True)
            else:
                card.set_downloaded(False)
        total = len(self._card_by_name or {})
        if total:
            if zip_ok:
                self._set_status(
                    f"已打包 zip · {os.path.basename(self._hitomi_zip_path())}",
                    self._done_ratio_color(done_n, total),
                )
            else:
                self._set_status(
                    f"已下 {done_n}/{total}",
                    self._done_ratio_color(done_n, total),
                )

    def _on_dl_detail(self, detail: dict):
        """zip 完成后的总结 + 自动下载结算标志。

        注意：worker 先 emit done_detail 再 emit ok；父类 _on_dl_ok 在
        _dl_summary_set 已 True 时不再改 _last_download_ok。
        此处必须写好成功/失败，否则 finished 结算会误记「败」。
        """
        failed = list(detail.get("failed") or [])
        self._last_failed = failed
        failed_names = [
            (it.get("name") or "") for it in failed if it.get("name")
        ]
        self._refresh_card_states(failed_names=failed_names)
        ok_n = int(detail.get("ok_n") or 0)
        fail_n = int(detail.get("fail_n") or 0)
        skip_n = int(detail.get("skip_n") or 0)
        zip_path = (detail.get("zip_path") or self._hitomi_zip_path() or "").replace("\\", "/")
        self._last_zip_path = zip_path
        # 校验：有 zip 且至少成功/跳过一项；允许部分张失败仍算「败」保留记录
        pure_skip = (
            fail_n == 0
            and skip_n > 0
            and ok_n > 0
            and skip_n >= ok_n
            and bool(zip_path)
        )
        validated = fail_n == 0 and (ok_n + skip_n) > 0 and bool(zip_path)
        if not validated and fail_n == 0 and ok_n > 0:
            # zip_path 偶发空：仍以成功张数为准
            validated = True
        self._last_download_ok = bool(validated)
        if pure_skip:
            self._last_batch_note = "已下载过"
        else:
            self._last_batch_note = "成功" if validated else "失败"

        if fail_n:
            summary = f"打包完成：成功 {ok_n} · 失败 {fail_n}"
            color = tk("warn")
        elif pure_skip:
            summary = f"已有 zip，已跳过 · {os.path.basename(zip_path)}"
            color = tk("ok")
        else:
            summary = f"打包完成：{ok_n} 张 → {os.path.basename(zip_path or '')}"
            color = tk("ok")
        self._dl_summary_set = True
        self._set_status(summary, color)
        self._log(summary, "ok" if fail_n == 0 else "warn")
        if zip_path:
            self._log(f"zip 路径：{zip_path}", "ok")

    def _start_download(
        self, images: list, *, skip_existing: bool = True, mode: str = "download", retries: int = 3
    ):
        if self._dl_worker and self._dl_worker.isRunning():
            return
        if self._parse_worker and self._parse_worker.isRunning():
            self._log("解析尚未结束", "warn")
            return
        if not images:
            self._log("没有可下载的项目", "warn")
            return
        # 整包 zip：始终用解析得到的完整列表（选中部分也打同一 zip，保证完整图集）
        full = list((self._info or {}).get("images") or images)
        if not full:
            full = list(images)
        save_dir = self._hitomi_save_dir()
        title = (self._info or {}).get("title") or "hitomi"
        zip_name = f"{_safe_folder_name(title, 'hitomi')}.zip"
        if _should_skip_by_record(self, zip_name):
            self._mark_already_downloaded(zip_name)
            return
        # 与抖音相同：不弹窗。同名 zip 先加序号打包，下完按大小判定真重复。
        skip_existing = False
        self._dup_compare_zip = ""
        try:
            from utils.download_confirm import next_numbered_file
            canonical = os.path.join(save_dir, zip_name)
            if os.path.isfile(canonical):
                new_zip = next_numbered_file(canonical)
                title = os.path.splitext(os.path.basename(new_zip))[0]
                self._dup_compare_zip = canonical
                self._log(
                    f"同名 zip 已存在 → 改存为 {os.path.basename(new_zip)}，"
                    "完成后按大小判定是否真重复",
                    "blue",
                )
        except Exception as e:
            self._log(f"同名预检异常（已忽略）：{e}", "warn")
        self._cancel[0] = False
        self._dl_summary_set = False
        self.btn_sel_dl.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self._set_status("准备打包 zip…", self._progress_gradient_color(0))
        self._emit_nav_progress(True, 0, mode="progress")
        # 官网 throttle 约 1s；页内间隔 0 时仍给 200ms 底限，避免打爆 CDN
        delay = self._delay_ms()
        if delay <= 0:
            delay = 200
        self._dl_worker = HitomiZipDownloadWorker(
            images=full,
            save_dir=save_dir,
            title=title,
            delay_ms=delay,
            skip_existing=skip_existing,
            cancel_flag=self._cancel,
            retries=retries,
            mode=mode,
        )
        self._dl_worker.progress.connect(self._on_dl_progress)
        self._dl_worker.ok.connect(self._on_dl_ok)
        self._dl_worker.err.connect(self._on_dl_err)
        self._dl_worker.log.connect(self._log)
        self._dl_worker.done_detail.connect(self._on_dl_detail)
        self._dl_worker.item_done.connect(self._on_item_done)
        self._dl_worker.finished_clean.connect(self._on_dl_finished)
        self._dl_worker.start()

    def export_settings(self) -> dict:
        return {
            "save_path": (self.save_edit.text() or "").strip(),
            "cookie_path": "",
            "check_records": _record_skip_enabled(self),
        }

    def apply_settings(self, d: dict):
        if not isinstance(d, dict):
            return
        path = (d.get("save_path") or "").strip()
        if path:
            self.save_edit.blockSignals(True)
            try:
                self.save_edit.setText(path.replace("\\", "/"))
            finally:
                self.save_edit.blockSignals(False)
            scan_dir_records("gallery_hitomi", path, mode="zip")
        if "check_records" in d:
            _gallery_set_record_skip(self, bool(d.get("check_records")))


def _paint_hitomi_icon(size: int = 20, selected: bool = True, dark: bool = True) -> QIcon:
    """hitomi.la 分页徽章：心形简标，青绿系区分 EH/Pixiv。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    s = float(size)
    if selected:
        pen_c = QColor("#F472B6") if dark else QColor("#DB2777")
        fill_c = QColor(pen_c)
        fill_c.setAlpha(40 if dark else 50)
    else:
        pen_c = QColor("#6b7280") if dark else QColor("#9aa3af")
        pen_c.setAlpha(160)
        fill_c = QColor(pen_c)
        fill_c.setAlpha(18)
    p.setPen(QPen(pen_c, max(1.1, s * 0.07)))
    p.setBrush(QBrush(fill_c))
    # 简化心形：两圆 + 三角
    r = s * 0.22
    p.drawEllipse(QPointF(s * 0.35, s * 0.38), r, r)
    p.drawEllipse(QPointF(s * 0.65, s * 0.38), r, r)
    from PyQt5.QtGui import QPolygonF
    poly = QPolygonF([
        QPointF(s * 0.16, s * 0.42),
        QPointF(s * 0.84, s * 0.42),
        QPointF(s * 0.50, s * 0.88),
    ])
    p.drawPolygon(poly)
    p.end()
    return QIcon(pm)


class PageGallery(QWidget):
    """图集下载：右侧分页容器。

    · 第 1 页「e-hentai.org」：当前的解析 + 下载完整内容（即 _GalleryEHentaiPage）。
    · 第 2 页「pixiv」：Pixiv 作品页解析与下载。
    · 第 3 页「hitomi.la」：无需 Cookie 的 hitomi 图库。

    分页交互对齐「视频下载」页（抖音 / B站 / YouTube）的 QTabWidget 折角样式。
    """

    # 侧栏「图集下载」下 2px 进度线（汇总各子页）
    nav_progress = pyqtSignal(bool, int, str)

    def __init__(self):
        super().__init__()
        apply_transparent_surface(self, "PageGallery")
        self.setStyleSheet(
            fmt(TAB_QSS + VIDEO_TAB_QSS)
            + "\n#PageGallery{background:transparent;border:none;}"
        )
        # 各子页最近一次进度；任一活跃则侧栏显示，都闲时隐藏
        self._nav_sources = {
            "eh": (False, 0, ""),
            "pixiv": (False, 0, ""),
            "hitomi": (False, 0, ""),
        }

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("VideoTabWidget")
        self.tabs.setIconSize(QSize(20, 20))
        try:
            from ui_main import install_tight_pre_dl_tab_bar
            install_tight_pre_dl_tab_bar(self.tabs)
        except Exception:
            try:
                self.tabs.tabBar().setElideMode(Qt.ElideNone)
                self.tabs.tabBar().setExpanding(False)
            except Exception:
                pass
        root.addWidget(self.tabs, 1)

        # 第 1 页：e-hentai.org —— 当前的完整内容
        self.page_eh = _GalleryEHentaiPage()
        _apply_gallery_tab_bg(self.page_eh, "TabGalleryEH")

        # 第 2 页：pixiv —— Pixiv 作品页解析与下载
        self.page_pixiv = _GalleryPixivPage()
        _apply_gallery_tab_bg(self.page_pixiv, "TabGalleryPixiv")

        # 第 3 页：hitomi.la —— 无需 Cookie
        self.page_hitomi = _GalleryHitomiPage()
        _apply_gallery_tab_bg(self.page_hitomi, "TabGalleryHitomi")

        self._pre_dl_tab_specs = (
            ("ehentai", "e-hentai.org"),
            ("pixiv", "pixiv"),
            ("hitomi", "hitomi.la"),
        )
        self._pre_dl_counts = {k: 0 for k, _ in self._pre_dl_tab_specs}
        self.tabs.addTab(self.page_eh, "e-hentai.org")
        self.tabs.addTab(self.page_pixiv, "pixiv")
        self.tabs.addTab(self.page_hitomi, "hitomi.la")
        self._install_record_skip_tab_slots()
        self._pre_dl_badge_host = self._install_pre_dl_tab_badges()
        self._refresh_tab_deco()
        self.tabs.currentChanged.connect(lambda _i: self._refresh_tab_deco())

        # 下载间隔（共享 · 右上角）
        corner = QWidget()
        corner.setStyleSheet("background: transparent;")
        corner_lay = QHBoxLayout(corner)
        corner_lay.setContentsMargins(0, 0, 0, 0)
        corner_lay.setSpacing(4)
        lbl_delay = QLabel("间隔")
        lbl_delay.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        corner_lay.addWidget(lbl_delay)
        self.cmb_delay = QComboBox()
        self.cmb_delay.setEditable(True)
        self.cmb_delay.setInsertPolicy(QComboBox.NoInsert)
        self.cmb_delay.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.cmb_delay.setMinimumWidth(80)
        self.cmb_delay.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        for label, ms in _DELAY_PRESETS:
            self.cmb_delay.addItem(label, ms)
        self._set_delay_ms(1200)
        self.cmb_delay.activated.connect(self._on_delay_activated)
        le_delay = self.cmb_delay.lineEdit()
        if le_delay is not None:
            le_delay.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            le_delay.setMinimumWidth(0)
            le_delay.editingFinished.connect(self._on_delay_edit_finished)
        corner_lay.addWidget(self.cmb_delay)
        self.tabs.setCornerWidget(corner)

        # 各子页共享下载间隔
        self.page_eh._gallery_delay_fn = self._delay_ms
        self.page_pixiv._gallery_delay_fn = self._delay_ms
        self.page_hitomi._gallery_delay_fn = self._delay_ms

        # 子页解析/下载进度 → 统一侧栏线
        try:
            self.page_eh.nav_progress.connect(
                lambda a, p, c: self._on_child_nav_progress("eh", a, p, c)
            )
            self.page_pixiv.nav_progress.connect(
                lambda a, p, c: self._on_child_nav_progress("pixiv", a, p, c)
            )
            self.page_hitomi.nav_progress.connect(
                lambda a, p, c: self._on_child_nav_progress("hitomi", a, p, c)
            )
        except Exception:
            pass

        theme.changed.connect(self.refresh_theme)

    def _install_record_skip_tab_slots(self):
        """e-hentai / hitomi 页签原图标位：正方形「防」开关（对齐侧栏「预」）。"""
        from ui_main import SidePreDlSlot

        bar = self.tabs.tabBar() if self.tabs is not None else None
        if bar is None:
            return
        for index, page in (
            (0, getattr(self, "page_eh", None)),
            (2, getattr(self, "page_hitomi", None)),
        ):
            if page is None:
                continue
            try:
                sw = SidePreDlSlot(
                    parent=bar, glyph="防", on_color=_RECORD_SKIP_YELLOW,
                )
                sw.setChecked(True)
                sw.setToolTip(_RECORD_SKIP_TIP)
                sw.clicked.connect(
                    lambda _=False, p=page, src=sw: _gallery_on_record_skip_toggled(p, src)
                )
                page.sw_record_skip = sw
                bar.setTabButton(index, QTabBar.LeftSide, sw)
                bar_sw = getattr(page, "sw_record_skip_bar", None)
                if bar_sw is not None:
                    bar_sw.setChecked(True)
            except Exception:
                log.exception("安装图集页签防重开关失败 index=%s", index)
                page.sw_record_skip = None
        refresh = getattr(bar, "_refresh_tab_layout", None)
        if callable(refresh):
            QTimer.singleShot(0, refresh)
        else:
            place = getattr(bar, "_place_left_buttons", None)
            if callable(place):
                QTimer.singleShot(0, place)

    def _install_pre_dl_tab_badges(self):
        """页签右侧内边距叠数字角标（不占按钮位，宽度不随条数变）。"""
        from ui_main import TabPreDlBadgeHost

        return TabPreDlBadgeHost(
            self.tabs, getattr(self, "_pre_dl_tab_specs", ()), parent=self
        )

    def set_pre_dl_counts(self, counts: dict):
        """子页页签右侧显示预下载条数；0 条隐藏角标，页签宽度不变。"""
        counts = counts or {}
        for key, _base in getattr(self, "_pre_dl_tab_specs", ()):
            try:
                n = max(0, int(counts.get(key, 0) or 0))
            except (TypeError, ValueError):
                n = 0
            self._pre_dl_counts[key] = n
        host = getattr(self, "_pre_dl_badge_host", None)
        if host is not None:
            host.set_counts(self._pre_dl_counts)

    def set_batch_counts(self, counts: dict):
        """子页页签显示批处理剩余「批N」；0 条隐藏。"""
        counts = counts or {}
        data = {}
        for key, _base in getattr(self, "_pre_dl_tab_specs", ()):
            try:
                n = max(0, int(counts.get(key, 0) or 0))
            except (TypeError, ValueError):
                n = 0
            data[key] = n
        host = getattr(self, "_pre_dl_badge_host", None)
        if host is not None and hasattr(host, "set_batch_counts"):
            host.set_batch_counts(data)

    def _apply_pre_dl_tab_texts(self):
        host = getattr(self, "_pre_dl_badge_host", None)
        if host is not None:
            host.set_counts(getattr(self, "_pre_dl_counts", {}))

    def _refresh_tab_deco(self):
        """按选中态刷新：pixiv 图标明暗 + pane 左上圆角。EH/hitomi 用页签「防」开关，无图标。"""
        dark = bool(theme.is_dark)
        idx = self.tabs.currentIndex()
        self.tabs.setTabIcon(1, _paint_pixiv_icon(20, selected=(idx == 1), dark=dark))

        first = "true" if idx == 0 else "false"
        self.tabs.setProperty("firstSelected", first)
        st = self.tabs.style()
        if st is not None:
            st.unpolish(self.tabs)
            st.polish(self.tabs)
        self.tabs.update()
        bar = self.tabs.tabBar()
        place = getattr(bar, "_place_left_buttons", None) if bar is not None else None
        if callable(place):
            place()
        host = getattr(self, "_pre_dl_badge_host", None)
        if host is not None:
            host.reposition()

    def _on_child_nav_progress(self, source: str, active: bool, pct: int, color: str):
        """汇总各子页：当前有任务的子页驱动侧栏线；都空闲则隐藏。"""
        self._nav_sources[source] = (bool(active), int(pct if pct is not None else 0), color or "")
        if active:
            try:
                self.nav_progress.emit(True, int(pct if pct is not None else 0), color or "")
            except Exception:
                pass
            return
        for key, (oa, op, oc) in self._nav_sources.items():
            if key == source:
                continue
            if oa:
                try:
                    self.nav_progress.emit(True, op, oc)
                except Exception:
                    pass
                return
        try:
            self.nav_progress.emit(False, 0, "")
        except Exception:
            pass

    def shutdown(self):
        """主窗口关窗：停各子页 worker。"""
        for p in (
            getattr(self, "page_eh", None),
            getattr(self, "page_pixiv", None),
            getattr(self, "page_hitomi", None),
        ):
            if p is not None and hasattr(p, "shutdown"):
                try:
                    p.shutdown()
                except Exception:
                    pass
        self.nav_progress.emit(False, 0, "")

    def refresh_theme(self, *_):
        self.setStyleSheet(
            fmt(TAB_QSS + VIDEO_TAB_QSS)
            + "\n#PageGallery{background:transparent;border:none;}"
        )
        if hasattr(self.page_eh, "refresh_theme"):
            self.page_eh.refresh_theme()
        if hasattr(self.page_pixiv, "refresh_theme"):
            self.page_pixiv.refresh_theme()
        if hasattr(self, "page_hitomi") and hasattr(self.page_hitomi, "refresh_theme"):
            self.page_hitomi.refresh_theme()
        _apply_gallery_tab_bg(self.page_eh, "TabGalleryEH")
        _apply_gallery_tab_bg(self.page_pixiv, "TabGalleryPixiv")
        if getattr(self, "page_hitomi", None) is not None:
            _apply_gallery_tab_bg(self.page_hitomi, "TabGalleryHitomi")
        self._refresh_tab_deco()
        host = getattr(self, "_pre_dl_badge_host", None)
        if host is not None:
            host.refresh_theme()

    # ── 下载间隔（由子页共享）───────────────────────────────────────────

    def _delay_ms(self) -> int:
        cmb = getattr(self, "cmb_delay", None)
        if cmb is None:
            return 1200
        idx = cmb.currentIndex()
        if 0 <= idx < cmb.count():
            data = cmb.itemData(idx)
            label = cmb.itemText(idx)
            cur = (cmb.currentText() or "").strip()
            if data is not None and cur in (label, _format_delay_ms(int(data))):
                return _clamp_delay_ms(int(data))
        return _parse_delay_to_ms(cmb.currentText())

    def _set_delay_ms(self, ms: int):
        cmb = getattr(self, "cmb_delay", None)
        if cmb is None:
            return
        ms = _clamp_delay_ms(ms)
        cmb.blockSignals(True)
        try:
            hit = -1
            for i in range(cmb.count()):
                data = cmb.itemData(i)
                if data is not None and int(data) == ms:
                    hit = i
                    break
            if hit >= 0:
                cmb.setCurrentIndex(hit)
            else:
                cmb.setCurrentIndex(-1)
                cmb.setEditText(_format_delay_ms(ms))
        finally:
            cmb.blockSignals(False)

    def _on_delay_activated(self, index: int):
        cmb = self.cmb_delay
        if 0 <= index < cmb.count():
            data = cmb.itemData(index)
            if data is not None:
                self._set_delay_ms(int(data))

    def _on_delay_edit_finished(self):
        ms = _parse_delay_to_ms(self.cmb_delay.currentText())
        self._set_delay_ms(ms)

    def handoff_fill_missing(
        self,
        source_url: str,
        gallery_folder: str,
        missing_names,
        *,
        report_path: str = "",
    ) -> bool:
        """序列文件检查「补全」：仅 e-hentai.org / exhentai.org。

        其它站点下载形态不同（Pixiv 按张、hitomi 整包 zip 等），不接入此流程。
        """
        url = (source_url or "").strip()
        low = url.lower()
        if "e-hentai.org" not in low and "exhentai.org" not in low:
            try:
                show_cursor_toast("序列补全", "仅支持e站", accent="warn")
            except Exception:
                pass
            return False
        page = getattr(self, "page_eh", None)
        if page is None:
            return False
        try:
            self.tabs.setCurrentWidget(page)
        except Exception:
            pass
        return bool(
            page.handoff_fill_missing(
                url, gallery_folder, missing_names, report_path=report_path
            )
        )

    # ── 设置导入导出：以第 1 页（e-hentai.org，当前唯一落地站点）为准 ──

    def export_settings(self) -> dict:
        d = self.page_eh.export_settings()
        d["delay_ms"] = self._delay_ms()
        # 各子页保存路径：以 EH 为主；hitomi/pixiv 可独立
        try:
            d["pixiv"] = self.page_pixiv.export_settings()
        except Exception:
            pass
        try:
            d["hitomi"] = self.page_hitomi.export_settings()
        except Exception:
            pass
        return d

    def apply_settings(self, d: dict):
        self.page_eh.apply_settings(d)
        if "delay_ms" in d:
            try:
                self._set_delay_ms(int(d.get("delay_ms")))
            except (TypeError, ValueError):
                pass
        # 共用主 save_path 到 pixiv（若子段未单独配置；独立 prefs 见 gallery_pixiv）
        base_path = (d.get("save_path") or "").strip().replace("\\", "/")
        try:
            px = dict(d.get("pixiv") or {})
            if base_path and not (px.get("save_path") or "").strip():
                px["save_path"] = base_path
            self.page_pixiv.apply_settings(px)
        except Exception:
            pass
        # hitomi.la：必须落在公共根目录下的 hitomi.la，勿复用 EH 路径
        try:
            hm = dict(d.get("hitomi") or {})
            hp = (hm.get("save_path") or "").strip().replace("\\", "/")
            if not hp or not hp.rstrip("/").endswith("hitomi.la"):
                root = ""
                if base_path.rstrip("/").endswith("e-hentai.org"):
                    root = base_path[: -len("e-hentai.org")].rstrip("/\\")
                elif base_path:
                    root = base_path.rstrip("/\\")
                elif hp:
                    # 历史错误：常写成公共根本身（如 ~/Downloads）
                    root = hp.rstrip("/\\")
                if root:
                    hm["save_path"] = os.path.join(root, "hitomi.la").replace("\\", "/")
                else:
                    hm["save_path"] = os.path.join(
                        os.path.expanduser("~/Downloads"), "hitomi.la"
                    ).replace("\\", "/")
            self.page_hitomi.apply_settings(hm)
        except Exception:
            pass

    # ── 向后兼容：旧代码里 self.page_gallery.url_edit / ._start_flow 等
    #    直接访问的都是「e-hentai.org」分页（第 1 页，真正接了后台逻辑的那页）──

    def __getattr__(self, name):
        page_eh = self.__dict__.get("page_eh")
        if page_eh is not None and hasattr(page_eh, name):
            return getattr(page_eh, name)
        raise AttributeError(
            f"'{type(self).__name__}' object has no attribute '{name}'"
        )
