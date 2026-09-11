# utils/user_prefs.py
# ---------------------------------------------------------------------------
# 用户习惯持久化：data/user.txt（JSON 文本）
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

# 本程序 user.txt 必须具备的顶层段（至少命中这些才算「像本程序配置」）
_APP_SECTIONS = ("fast_save", "screenshot", "ui")


def _project_root() -> str:
    from utils.app_paths import app_root
    return app_root()


def records_dir() -> str:
    from utils.app_paths import records_dir as _rd
    return _rd()


def user_file_path() -> str:
    """data/user.txt（用户说的「user 文本文件」）。"""
    from utils.app_paths import records_file
    return records_file("user.txt")


def default_clipboard_whitelist() -> Dict[str, list]:
    """预下载域名白名单默认值（关于页可编辑，按下载子页分组）。

    匹配规则：只认剪贴板里 https 链接的主机名（www.douyin.com 可命中 douyin.com）。
    正文里提到域名、或 AV1 这类字样，不算进白名单。
    """
    return {
        # 子串匹配（小写）；v.douyin.com 含 douyin.com 即可命中
        "douyin": ["douyin.com", "tiktok.com", "iesdouyin.com"],
        "bilibili": ["bilibili.com", "b23.tv", "bili2233.cn"],
        "youtube": ["youtube.com", "youtu.be"],
        "gallery_eh": ["e-hentai.org", "exhentai.org"],
        "gallery_pixiv": ["pixiv.net"],
        "gallery_hitomi": ["hitomi.la"],
    }


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
            "side_order": [
                "overview", "fast", "video", "gallery",
                "shot", "region_rec",
                "@hdr:assistant",
                "paste", "img_proc", "points", "voice", "voice_clone", "game_assist", "prompt",
                "@hdr:tool",
                "ratio", "tz_fx", "dir_link",
            ],
            # 侧栏分区标题自定义（assistant / tool 等）；空则用出厂名
            "section_names": {},
        },
        # 公共下载根目录（系统总览「公共保存」；各业务子目录由此派生）
        "save_path_base": "",
        # 语音录入：录音方式等
        "voice_input": {
            "record_mode": "click",  # hold=A 按住松手 | click=B 点击切换
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
        "game_assist": {
            "sources": [],
            "groups": [],   # 知识库用户分类：[{gid,title,active}]；「未分类」为内置，不存
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "",
            "model": "",
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
            "whitelist": default_clipboard_whitelist(),
        },
        # 视频下载页（抖音/B站/YouTube）：各子页保存路径 + Cookie路径
        "video": {
            "douyin": {"save_path": "", "cookie_path": ""},
            "bilibili": {"save_path": "", "cookie_path": ""},
            "youtube": {"save_path": "", "cookie_path": ""},
        },
        # 积分计算：常用汇率等
        "points_calc": {
            "rate": 7.25,       # USD → CNY
            "currency": "CNY",
            "history_view": "image",  # 历史记录子页：image=生图 / video=生视频
        },
        # 电子钟：七段数字颜色（与粘贴助手同套七色）
        "clock": {
            "digit_color": "#eab308",
        },
        # 提示词：左右两栏 10 项正文（重开软件继续改）
        "prompt_editor": {
            "orig": {},
            "new": {},
            "custom_presets": {},
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
            "save_format": "jpg",     # jpg | png（四区共用）
            "split_format": "jpg",    # 兼容旧键，与 save_format 同步
            "convert_format": "jpg",
            "upscale_model": "realesrgan-x4plus",
            "upscale_scale": 4,       # 2 … 6
            "active_op": "scale",     # scale | upscale | split | more
        },
    }


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
        merged = _deep_merge(default_prefs(), data)
        return merged, False
    except Exception:
        bak = _quarantine_bad_user_file(path, reason="read_error")
        log.exception("读取 user.txt 异常，已隔离 bak=%s", bak)
        return default_prefs(), True


def save_user_prefs(prefs: Dict[str, Any]) -> bool:
    """原子写入 data/user.txt。成功返回 True。"""
    path = user_file_path()
    try:
        payload = _deep_merge(default_prefs(), prefs or {})
        payload["version"] = _PREFS_VERSION
        # 确保核心段齐全
        base = default_prefs()
        for key in (
            *_APP_SECTIONS,
            "gallery", "gallery_pixiv", "points_calc", "clipboard_auto",
            "video", "region_record", "voice_input", "voice_clone", "game_assist", "digital_human",
        ):
            if not isinstance(payload.get(key), dict):
                payload[key] = deepcopy(base[key])
        if "save_path_base" not in payload:
            payload["save_path_base"] = base.get("save_path_base", "")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
        log.debug("user.txt 已保存 path=%s", path)
        return True
    except Exception:
        log.exception("保存 user.txt 失败 path=%s", path)
        return False
