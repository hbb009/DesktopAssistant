# utils/user_prefs.py
# ---------------------------------------------------------------------------
# 用户习惯持久化：userdata/user.txt（JSON 文本）
#
# 启动时严格判断（不能只靠「文件是否存在」）：
#   · 无文件 / 空文件 → 按当前程序状态新建
#   · 非 JSON / 不是 dict → 无效
#   · 缺少本程序特征字段（version + 业务段）→ 视为他用文件，隔离后重建
#   · 合法 → 读入并恢复到界面
# 运行中 / 关闭时：
#   · 设置变更后防抖写回；关窗再完整写一次
# ---------------------------------------------------------------------------

from __future__ import annotations

import json
import os
import shutil
import time
from copy import deepcopy
from typing import Any, Dict, Optional, Tuple

from utils.logger import get_logger

log = get_logger(__name__)

_PREFS_VERSION = 1

# 侧栏主菜单出厂顺序（含分区槽位 @hdr:xxx）；ui_main._default_side_order 同源引用。
# 只决定「画在哪」：页面分类（电子钟空闲 / 按住不切）仍按 ui_main._SIDE_SECTION_MAP。
DEFAULT_SIDE_ORDER = (
    "video", "audio", "gallery", "fast", "shot", "region_rec",
    "@hdr:assistant",
    "paste", "img_proc", "pixel_board", "voice", "auk", "points", "sub_usage", "ratio", "prompt", "ollama", "text_edit", "tz_fx", "dir_link",
    "@hdr:tool",
    "gameassist", "gamebrain", "gamemeet",
)

# 本程序 user.txt 必须具备的顶层段（至少命中这些才算「像本程序配置」）
_APP_SECTIONS = ("fast_save", "screenshot", "ui")


def _project_root() -> str:
    from utils.app_paths import app_root
    return app_root()


def records_dir() -> str:
    from utils.app_paths import records_dir as _rd
    return _rd()


def user_file_path() -> str:
    """userdata/user.txt — single canonical prefs path."""
    from utils.app_paths import userdata_dir
    return os.path.join(userdata_dir(), "user.txt")


def normalize_whitelist_pattern(raw: str) -> str:
    """白名单条目：去协议、去查询串，保留 host 或 host/path。"""
    s = (raw or "").strip().lower()
    if not s:
        return ""
    for prefix in ("https://", "http://"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    s = s.split("?")[0].split("#")[0].strip()
    if s.startswith("www."):
        s = s[4:]
    s = s.strip(".")
    s = s.rstrip("/")
    return s


def url_hits_whitelist_patterns(url: str, patterns) -> bool:
    """判断一条 https URL 是否命中白名单（主机名或 host/path 前缀）。"""
    from urllib.parse import urlparse

    u = (url or "").strip()
    if not u:
        return False
    try:
        p = urlparse(u)
    except Exception:
        return False
    host = (p.hostname or "").lower().strip(".")
    path = (p.path or "").lower() or "/"
    if not path.startswith("/"):
        path = "/" + path
    if not host:
        return False
    for raw in patterns or []:
        pat = normalize_whitelist_pattern(str(raw or ""))
        if not pat:
            continue
        if "/" in pat:
            ph, _, rest = pat.partition("/")
            want = "/" + rest.strip("/")
            if not (host == ph or host.endswith("." + ph)):
                continue
            if path == want or path.startswith(want + "/"):
                return True
        elif host == pat or host.endswith("." + pat):
            return True
    return False


# 旧版把「其它视频」拆成三条；读档时并回 other。
_OTHER_LEGACY_KEYS = ("qishui", "jimeng", "liblib")


def _clean_whitelist_patterns(raw) -> list:
    out = []
    seen = set()
    if not isinstance(raw, list):
        return out
    for x in raw:
        s = str(x or "").strip().lower()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def other_whitelist_patterns(wl: Optional[dict] = None) -> list:
    """其它视频子页的域名。新键 other；旧档仍是 qishui / jimeng / liblib。"""
    wl = wl if isinstance(wl, dict) else {}
    if isinstance(wl.get("other"), list):
        return _clean_whitelist_patterns(wl.get("other"))
    merged = []
    seen = set()
    found = False
    for key in _OTHER_LEGACY_KEYS:
        raw = wl.get(key)
        if isinstance(raw, list):
            found = True
            for s in _clean_whitelist_patterns(raw):
                if s not in seen:
                    seen.add(s)
                    merged.append(s)
    if found:
        return merged
    return list(default_clipboard_whitelist().get("other") or [])


def default_clipboard_whitelist() -> Dict[str, list]:
    """预下载域名白名单默认值（关于页可编辑，按下载子页分组）。

    视频 4 条（抖音 / B站 / YouTube / 其它视频）+ 音频 1 条（汽水音乐）+ 图集 3 条。
    其它视频含即梦、LibTV、XVideos、Pornhub。汽水单独成键，检测时必须早于 douyin.com。
    匹配规则：只认剪贴板里 https 链接。条目可以是主机名（www.douyin.com 可命中 douyin.com），
    也可以带路径前缀（music.douyin.com/qishui）。正文里提到域名不算。
    """
    return {
        # 子串匹配（小写）；v.douyin.com 含 douyin.com 即可命中
        "douyin": ["douyin.com", "tiktok.com", "iesdouyin.com"],
        "bilibili": ["bilibili.com", "b23.tv", "bili2233.cn"],
        "youtube": ["youtube.com", "youtu.be"],
        # 汽水路径必须比 douyin.com 更具体，检测时先看本条
        "qishui": list(_QISHUI_WHITELIST),
        "other": [
            "jimeng.jianying.com",
            "jianying.com",
            "jimeng.com",
            "liblib.tv",
            "xvideos.com",
            "pornhub.com",
        ],
        "gallery_eh": ["e-hentai.org", "exhentai.org"],
        "gallery_pixiv": ["pixiv.net"],
        "gallery_hitomi": ["hitomi.la"],
    }


# 白名单迁移版本：clipboard_auto.whitelist_rev。
# 2 = 其它视频追加 xvideos.com（只补一次；之后用户删掉不再加回）。
# 3 = 汽水音乐从「其它视频」拆到独立键 qishui（只搬走已有条目，不把删掉的加回）。
# 4 = 其它视频追加 pornhub.com（只补一次；之后用户删掉不再加回）。
# 不放进 default_prefs：否则旧档合并默认值后会被误认为已迁移。
_WHITELIST_REV = 4
_QISHUI_WHITELIST = (
    "music.douyin.com/qishui",
    "qishui.douyin.com",
)
# rev 2 / rev 4 都是「往 other 补一个域名」，只补一次
_OTHER_NEW_DOMAINS = {
    2: ("xvideos.com",),
    4: ("pornhub.com",),
}


def migrate_clipboard_whitelist(prefs: Dict[str, Any]) -> bool:
    """按 whitelist_rev 逐级升级旧档白名单；改动了返回 True。"""
    if not isinstance(prefs, dict):
        return False
    ca = prefs.get("clipboard_auto")
    if not isinstance(ca, dict):
        return False
    try:
        rev = int(ca.get("whitelist_rev") or 0)
    except (TypeError, ValueError):
        rev = 0
    if rev >= _WHITELIST_REV:
        return False
    changed = False
    wl = ca.get("whitelist")
    if not isinstance(wl, dict):
        wl = {}
        ca["whitelist"] = wl
        changed = True
    if rev < 2:
        other = wl.get("other")
        if not isinstance(other, list):
            other = other_whitelist_patterns(wl)
        other = list(other)
        if "xvideos.com" not in [str(x or "").strip().lower() for x in other]:
            other.append("xvideos.com")
        wl["other"] = other
        changed = True
    if rev < 3:
        other = wl.get("other")
        if not isinstance(other, list):
            other = other_whitelist_patterns(wl)
        other = _clean_whitelist_patterns(other)
        moved = [p for p in other if p in _QISHUI_WHITELIST or "qishui" in p]
        wl["other"] = [p for p in other if p not in moved]
        # 合并进默认表时会先塞入出厂汽水域名；这里以用户「其它视频」里实际有的为准
        wl["qishui"] = moved
        changed = True
    if rev < 4:
        other = wl.get("other")
        if not isinstance(other, list):
            other = other_whitelist_patterns(wl)
        other = _clean_whitelist_patterns(other)
        have = {str(x or "").strip().lower() for x in other}
        for dom in _OTHER_NEW_DOMAINS[4]:
            if dom not in have:
                other.append(dom)
        wl["other"] = other
        changed = True
    ca["whitelist_rev"] = _WHITELIST_REV
    return True


def _stamp_whitelist_rev(prefs: Dict[str, Any]) -> None:
    """写盘时标记已是当前白名单版本（新建 / 重置出的档不再触发迁移）。"""
    ca = prefs.get("clipboard_auto") if isinstance(prefs, dict) else None
    if not isinstance(ca, dict):
        return
    try:
        rev = int(ca.get("whitelist_rev") or 0)
    except (TypeError, ValueError):
        rev = 0
    ca["whitelist_rev"] = max(rev, _WHITELIST_REV)


def default_prefs() -> Dict[str, Any]:
    return {
        "version": _PREFS_VERSION,
        "fast_save": {
            "enabled": False,
            "save_path": "",
            "txt_hotkey": "F4",
            "mkdir_hotkey": "F8",
            "mkdir_name_f9": "Grok",
            "mkdir_name_f10": "Qwen",
            "check_ignore_ext": True,
            "check_fill": False,
            "dup_exts": [],
            "dup_exts_off": [],
        },
        "screenshot": {
            "enabled": False,
            "save_path": "",
            "format": "png",
            "hotkey_mod": "Alt",
            "hotkey_key": "A",
            "auto_edit": False,
            "auto_copy": True,
            "hide_window": False,
            "detect_window": False,
        },
        # 区域录屏：路径由「关于」公共目录派生 …/RegionRecord
        # default_region: 全局逻辑像素 {x,y,w,h}；缺省或 w/h<=0 表示未设定
        "region_record": {
            "save_path": "",
            # 默认不自动藏主窗：hide 会让任务栏也没了，易被当成闪退
            "hide_window": False,
            "show_cursor": True,
            "default_region": None,
        },
        "ui": {
            "theme": "dark",   # light | dark（默认深色）
            "scale": 1.0,      # 界面缩放：0.75 / 1.0 / 1.25 / 1.5
            # 主窗口尺寸（逻辑像素；出厂默认 / 最小 960×776，可放大）
            "window_w": 960,
            "window_h": 776,
            # 侧栏主菜单顺序（含分区槽位 @hdr:xxx；可拖拽改写，落盘到 user.txt）
            "side_order": list(DEFAULT_SIDE_ORDER),
            # 侧栏分区标题自定义（assistant / tool 等）；空则用出厂名
            "section_names": {},
        },
        # 公共下载根目录（系统总览「公共保存」；各业务子目录由此派生）
        "save_path_base": "",
        "save_subdir_names": {},
        # 上次写入公共保存路径时的本机身份（换电脑后用来丢弃旧绝对路径）
        "save_path_host": {"computer": "", "user": "", "home": ""},
        # 语音录入：录音方式等
        "voice_input": {
            "record_mode": "click",  # hold=A 按住松手 | click=B 点击切换
            "model": "",             # 空=页面按本机模型挑默认
            "language": "zh",
        },
        # 文本编辑：前缀符号历史（原属于语音录入，v9.17 拆分后独立成页）
        "text_editor": {
            "symbol_history": [],
        },
        "voice_clone": {
            "prompt_wav": "",
            "prompt_wavs": [],
            "prompt_text": "",
            "tts_text": "",
            "instruct": "",
            "speed": 1.0,
            "save_path": "",
            "model_dir": "",
        },
        "gameassist": {
            "sources": [],
            "groups": [],   # 知识库用户分类：[{gid,title,active}]；「未分类」为内置，不存
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "",
            "model": "",
        },
        "gamebrain": {
            "sources": [],
            "groups": [],
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "",  # 留空：在页面填写，或用环境变量 OPENROUTER_API_KEY
            "model": "typesafe/jev-1.13",
        },
        "digital_human": {
            "image": "",
            "audio": "",
            "save_path": "",
        },
        # 图集下载页（间隔 / 保存路径 / 勾选等）
        # hitomi 嵌套段：公共根目录下的 hitomi.la（与 e-hentai.org / pixiv 同级）
        "gallery": {
            "save_path": "",
            "cookie_path": "",
            "delay_ms": 1200,
            "auto_download": True,
            # 页底「已下记录」左侧开关：开=读 gallery_eh.txt 防重下
            "check_records": True,
            "hitomi": {"save_path": "", "cookie_path": "", "check_records": True},
        },
        # 图集下载 · Pixiv 分页（独立于 e-hentai 的 Cookie / 保存路径等）
        "gallery_pixiv": {
            "save_path": "",
            "cookie_path": "",
            "delay_ms": 1200,
            "auto_download": True,
        },
        # 预下载：剪贴板白名单入队（关于页可改）
        # auto_process：系统总览「预下载」开关，写入 user.txt
        # video/gallery 为旧侧栏键，保留以免旧版 user.txt 读档异常
        "clipboard_auto": {
            "video": False,
            "gallery": False,
            "auto_process": False,
            # 侧栏剪贴板归属："" | paste | gamebrain | gamemeet（互斥，默认无）
            "clipboard_owner": "",
            # 静默复制（全局 F7，把前台选中文字推入归属页）总开关；关=注销 F7
            "silent_copy": True,
            "whitelist": default_clipboard_whitelist(),
        },
        # 视频下载页（抖音/B站/YouTube/其它视频）：各子页保存路径 + Cookie路径
        # 其它视频（即梦 / LibTV / XVideos / Pornhub）共用 other，统一落到公共保存根下 Video
        # check_records：页底已下记录开关。开=按即梦/LibTV/XVideos/Pornhub 的类型和画质跳过 other_video.txt 里已有的那一档
        "video": {
            "douyin": {"save_path": "", "cookie_path": ""},
            "bilibili": {"save_path": "", "cookie_path": ""},
            "youtube": {"save_path": "", "cookie_path": ""},
            "other": {"save_path": "", "cookie_path": "", "check_records": True},
        },
        # 音频下载 · 汽水音乐（公共保存根下 Audio）
        # save_subdir_names：公共保存各子目录的文件夹名，空则用出厂名
        # check_records：页底防重开关。开=按视频/音频跳过 qishui.txt 里已有的那一项
        "audio": {
            "qishui": {"save_path": "", "cookie_path": "", "check_records": True},
        },
        # 积分计算：常用汇率等
        "points_calc": {
            "rate": 7.25,       # USD → CNY
            "currency": "CNY",
            "history_view": "image",  # 历史记录子页：image=生图 / video=生视频
        },
        # 订阅用量：缺这一段时给出四条空白平台。已保存的 items 整表替换，删掉的不会补回。
        "sub_usage": {
            "display": "forward",
            "items": [
                {
                    "id": "runninghub", "name": "runninghub.ai", "kind": "points", "meter": "points",
                    "record_dir": "forward", "points_remain": 100,
                    "monthly_points": 0, "renew_day": 1, "expire": "",
                    "window_hours": 5, "window_reset": "", "window_remain": 100,
                    "week_weekday": 0, "week_time": "08:00", "week_reset": "", "week_remain": 100,
                },
                {
                    "id": "chatgpt", "name": "ChatGPT", "kind": "month", "record_dir": "forward",
                    "monthly_points": 0, "renew_day": 1, "expire": "",
                    "window_hours": 5, "window_reset": "", "window_remain": 100,
                    "week_weekday": 0, "week_time": "08:00", "week_reset": "", "week_remain": 100,
                },
                {
                    "id": "gemini", "name": "Gemini", "kind": "month", "record_dir": "reverse",
                    "monthly_points": 0, "renew_day": 1, "expire": "",
                    "window_hours": 5, "window_reset": "", "window_remain": 0,
                    "week_weekday": 0, "week_time": "08:00", "week_reset": "", "week_remain": 0,
                },
                {
                    "id": "grok", "name": "Grok Bot", "kind": "year", "record_dir": "forward",
                    "monthly_points": 0, "renew_day": 1, "expire": "",
                    "window_hours": 5, "window_reset": "", "window_remain": 100,
                    "week_weekday": 0, "week_time": "08:00", "week_reset": "", "week_remain": 100,
                },
            ],
        },
        # 电子钟：七段数字颜色（与粘贴助手同套七色）
        "clock": {
            "digit_color": "#eab308",
        },
        # 目录映射：是否用管理员权限执行 mklink
        "dir_link": {
            "run_as_admin": True,
        },
        # 提示词：分页各自一份正文和预设。pages 为空时，页面会把旧的
        # 顶层 orig / custom_presets 收进标题「默认」的第一页。
        "prompt_editor": {
            "active": "",
            "pages": [],
            "full_lang": "zh",
            "translate_model": "",
        },
        # 旧角色模板残留，仅给提示词页一次性回读装备/场景细节/色彩
        "female_char": {
            "fields": {},
            "body": "",
            "body_locked": True,
            "custom_modules": {},
        },
        # 图片处理：输出目录由公共根派生 …/ImageProc；JPG 固定最高品质
        "image_proc": {
            "save_path": "",
            "scale_pct": 50,          # 25 / 50 / 75
            "save_format": "jpg",     # jpg | png（功能区共用；抠图强制 png）
            "split_format": "jpg",    # 兼容旧键，与 save_format 同步
            "convert_format": "jpg",
            "upscale_model": "realesrgan-x4plus",
            "upscale_scale": 4,       # 2 … 6
            "active_op": "scale",     # scale | upscale | split | matting | more
        },
        # 像素画板：画板和行走各记各的。行走沿用 cell/colors/name/dollar；画板用 art_*。图本身不进 user.txt。
        "pixel_board": {
            "save_path": "",
            "cell": 48,
            "colors": 0,
            "stride": 2,
            "leg": 42,
            "fps": 8,
            "four": False,
            "dollar": True,
            "name": "角色",
        },
        "timezone_fx": {
            "cities": [
                "洛杉矶", "纽约", "伦敦", "巴黎",
                "迪拜", "加尔各答", "上海", "东京",
            ],
            "hero_city": "上海",
        },
        # 看图：勾选的扩展名会关联到 PhotoViewer；空列表表示还没关联。
        # pierce：stay=翻到头仍留在本夹，next=接着进旁边的文件夹。
        # lock_scale：翻页时沿用当前页的缩放比例。
        # auto_window：换图时窗口按图片显示尺寸自己改变（与 * 相同，不退出最大化）。
        # auto_fit：打开图片时按桌面自适应。缺省为开，和原来的默认一致。
        "photo_viewer": {
            "exts": [],
            "pierce": "stay",
            "lock_scale": False,
            "auto_window": False,
            "auto_fit": True,
            "prev_prog": {},
        },
        # 文件阅读器：打开的扩展名会关联到 MarkdownViewer；空列表表示还没关联。
        "md_reader": {
            "exts": [],
            "prev_prog": {},
        },
    }


def _migrate_gameassist_prefs(data: Dict[str, Any]) -> Dict[str, Any]:
    """旧键 game_assist → 合体名 gameassist（含侧栏顺序）。"""
    if not isinstance(data, dict):
        return data
    if "game_assist" in data:
        if "gameassist" not in data:
            data["gameassist"] = data.pop("game_assist")
        else:
            data.pop("game_assist", None)
    ui = data.get("ui")
    if isinstance(ui, dict):
        order = ui.get("side_order")
        if isinstance(order, list):
            ui["side_order"] = [
                "gameassist" if x == "game_assist" else x for x in order
            ]
    return data


def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """用 overlay 覆盖 base（嵌套 dict 递归合并，其它类型整段替换）。"""
    out = deepcopy(base)
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def is_valid_user_prefs_payload(data: Any) -> bool:
    """判断解析后的内容是否为本程序的 user 配置。

    规则（同时满足）：
      1. 顶层必须是 JSON 对象（dict）
      2. version 为整数，且在合理范围（1 … 当前版本+若干）
      3. 至少包含 2 个已知业务段，且值为 dict
         （允许未来增删字段；但「随便一个 json 对象」不算本程序文件）
    """
    if not isinstance(data, dict) or not data:
        return False

    ver = data.get("version")
    if not isinstance(ver, int):
        return False
    # 过旧/乱写版本号视为无效；允许略高于当前（向前兼容小版本）
    if ver < 1 or ver > _PREFS_VERSION + 20:
        return False

    section_hits = 0
    for key in _APP_SECTIONS:
        val = data.get(key)
        if isinstance(val, dict):
            section_hits += 1
    # 至少 2 段：避免仅有 {"version":1,"foo":...} 的伪装文件
    if section_hits < 2:
        return False

    return True


def _quarantine_bad_user_file(path: str, reason: str = "invalid") -> Optional[str]:
    """把无效/他用 user.txt 挪到旁路备份，避免直接覆盖后无法找回。

    返回备份路径；失败返回 None。
    """
    try:
        if not os.path.isfile(path):
            return None
        # 空文件没必要备份
        try:
            if os.path.getsize(path) == 0:
                os.remove(path)
                return None
        except Exception:
            pass
        ts = time.strftime("%Y%m%d_%H%M%S")
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (reason or "invalid"))
        bak = os.path.join(
            os.path.dirname(path),
            f"user.invalid.{safe}.{ts}.txt",
        )
        # 避免重名
        n = 1
        base_bak = bak
        while os.path.exists(bak):
            bak = base_bak.replace(".txt", f".{n}.txt")
            n += 1
        shutil.move(path, bak)
        return bak
    except Exception:
        return None


def diagnose_user_file(path: Optional[str] = None) -> str:
    """启动诊断：返回简短状态码，便于日志/调试。

    missing | empty | bad_json | not_object | foreign | ok
    """
    path = path or user_file_path()
    if not os.path.isfile(path):
        return "missing"
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception:
        return "bad_json"
    if not raw.strip():
        return "empty"
    try:
        data = json.loads(raw)
    except Exception:
        return "bad_json"
    if not isinstance(data, dict):
        return "not_object"
    if not is_valid_user_prefs_payload(data):
        return "foreign"
    return "ok"


def load_user_prefs() -> Tuple[Dict[str, Any], bool]:
    """读取用户设置。

    返回 (prefs, created_new)：
      created_new=True 表示文件缺失/空/无效/非本程序内容，
      调用方应按当前程序状态 save 一次重建合法 user.txt。
    """
    path = user_file_path()
    status = diagnose_user_file(path)

    if status == "missing":
        return default_prefs(), True

    if status == "empty":
        # 空文件：删掉后按新建处理
        try:
            os.remove(path)
        except Exception:
            log.exception("删除空 user.txt 失败 path=%s", path)
        log.info("user.txt 为空，将新建默认配置")
        return default_prefs(), True

    if status in ("bad_json", "not_object", "foreign"):
        bak = _quarantine_bad_user_file(path, reason=status)
        log.warning("user.txt 无效 status=%s，已隔离 bak=%s", status, bak)
        return default_prefs(), True

    # ok
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.loads(f.read())
        if not is_valid_user_prefs_payload(data):
            # 二次校验失败
            bak = _quarantine_bad_user_file(path, reason="foreign")
            log.warning("user.txt 二次校验失败，已隔离 bak=%s", bak)
            return default_prefs(), True
        log.debug("user.txt 读取成功 path=%s", path)
        data = _migrate_gameassist_prefs(data)
        merged = _deep_merge(default_prefs(), data)
        try:
            from utils.app_paths import rebase_public_save_prefs
            if rebase_public_save_prefs(merged):
                log.info("已按本机重定向公共保存目录 → %s", merged.get("save_path_base") or "")
        except Exception:
            log.exception("重定向公共保存目录失败")
        try:
            from utils.app_paths import migrate_video_other_subdir
            if migrate_video_other_subdir(merged):
                log.info("其它视频保存目录已统一为公共保存根下 Video")
        except Exception:
            log.exception("迁移其它视频保存目录失败")
        try:
            if migrate_clipboard_whitelist(merged):
                log.info("预下载白名单已升级 rev=%s", _WHITELIST_REV)
        except Exception:
            log.exception("迁移预下载白名单失败")
        try:
            base = (merged.get("save_path_base") or "").strip().replace("\\", "/").rstrip("/")
            audio = merged.get("audio")
            if not isinstance(audio, dict):
                audio = {}
                merged["audio"] = audio
            sub = audio.get("qishui")
            if not isinstance(sub, dict):
                sub = {}
                audio["qishui"] = sub
            path = (sub.get("save_path") or "").replace("\\", "/").rstrip("/")
            target = (base + "/Audio") if base else ""
            was_qishui = bool(base) and path == base + "/Qishui"
            if base and (not path or was_qishui):
                if was_qishui:
                    old_dir = os.path.join(base, "Qishui")
                    new_dir = os.path.join(base, "Audio")
                    if os.path.isdir(old_dir) and not os.path.exists(new_dir):
                        try:
                            os.rename(old_dir, new_dir)
                            log.info("音频目录已从 Qishui 改名为 Audio")
                        except Exception:
                            log.exception("音频目录改名为 Audio 失败")
                if path != target:
                    sub["save_path"] = target
            names = merged.get("save_subdir_names")
            if isinstance(names, dict) and names.get("audio.qishui") == "Qishui":
                names.pop("audio.qishui", None)
        except Exception:
            log.exception("补音频保存目录失败")
        return merged, False
    except Exception:
        bak = _quarantine_bad_user_file(path, reason="read_error")
        log.exception("读取 user.txt 异常，已隔离 bak=%s", bak)
        return default_prefs(), True


def save_user_prefs(prefs: Dict[str, Any]) -> bool:
    """原子写入 userdata/user.txt。成功返回 True。"""
    path = user_file_path()
    try:
        payload = _deep_merge(default_prefs(), prefs or {})
        payload["version"] = _PREFS_VERSION
        _stamp_whitelist_rev(payload)
        # 确保核心段齐全
        base = default_prefs()
        for key in (
            *_APP_SECTIONS,
            "gallery", "gallery_pixiv", "points_calc", "sub_usage", "clipboard_auto",
            "video", "region_record", "voice_input", "voice_clone", "gameassist", "gamebrain", "digital_human",
            "photo_viewer", "md_reader",
        ):
            if not isinstance(payload.get(key), dict):
                payload[key] = deepcopy(base[key])
        if "save_path_base" not in payload:
            payload["save_path_base"] = base.get("save_path_base", "")
        try:
            from utils.app_paths import current_save_host
            payload["save_path_host"] = current_save_host()
        except Exception:
            pass
        from utils.photo_viewer_cfg import external_auto_fit, note_saved_auto_fit, prefs_file_lock
        with prefs_file_lock(path):
            # 看图窗口按 / 改过自适应时，这次保存沿用磁盘上的新值。
            try:
                pv = payload.get("photo_viewer")
                if isinstance(pv, dict):
                    adopted = external_auto_fit(pv.get("auto_fit", True))
                    if adopted is not None:
                        pv["auto_fit"] = bool(adopted)
                        if isinstance(prefs, dict):
                            box = prefs.get("photo_viewer")
                            if not isinstance(box, dict):
                                box = {}
                                prefs["photo_viewer"] = box
                            box["auto_fit"] = bool(adopted)
            except Exception:
                log.exception("合并看图自适应失败")
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp, path)
        try:
            pv = payload.get("photo_viewer")
            if isinstance(pv, dict):
                note_saved_auto_fit(pv.get("auto_fit", True))
        except Exception:
            log.exception("记下看图自适应失败")
        log.debug("user.txt 已保存 path=%s", path)
        return True
    except Exception:
        log.exception("保存 user.txt 失败 path=%s", path)
        return False
