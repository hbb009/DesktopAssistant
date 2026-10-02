# utils/app_paths.py
# ---------------------------------------------------------------------------
# 路径中枢：开发态 = 项目根；打包态(onedir) = exe 所在目录（可写）
# 包内只读资源（assets/skills）走 resource_root()（_MEIPASS / _internal）
# ---------------------------------------------------------------------------

from __future__ import annotations

import json
import os
import sys


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")


def pip_python() -> str:
    """供 pip / 修复 bat 使用的 Python 可执行文件。

    打包后 sys.executable 是本程序 exe，不能 -m pip；改用 PATH 上的 python/py。
    """
    if not is_frozen():
        return sys.executable or "python"
    try:
        import shutil
        for name in ("python", "python3", "py"):
            p = shutil.which(name)
            if p:
                return p
    except Exception:
        pass
    return "python"


def pip_cmd_prefix() -> list:
    """返回可直接拼进 subprocess 的 [python, \"-m\", \"pip\", ...]。"""
    py = pip_python()
    # Windows 商店/启动器：py -3 更稳；which 到的 py.exe 需加 -3
    base = os.path.basename(py).lower()
    if base in ("py.exe", "py") and is_frozen():
        return [py, "-3", "-m", "pip"]
    return [py, "-m", "pip"]


def app_root() -> str:
    """可写根目录：exe 旁（打包）或项目根（开发）。

    运行数据一律放在此目录下的 data/。
    """
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    # utils/ 的上一级 = 项目根
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_root() -> str:
    """只读资源根：打包为 _MEIPASS，开发为项目根。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return meipass
    return app_root()


def resource_path(*paths: str) -> str:
    return os.path.join(resource_root(), *paths)


_MIGRATED = False
_MIGRATED_GA = False
_MIGRATED_VC = False
_MIGRATED_VE = False
_MIGRATED_FLAT = False
_MIGRATED_RECORDS_SPLIT = False


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _rmdir_if_empty(path: str) -> None:
    try:
        if os.path.isdir(path) and not os.listdir(path):
            os.rmdir(path)
    except Exception:
        pass


def _move_entry(src: str, dst: str) -> None:
    """把 src 挪到 dst。目标已存在则合并目录 / 跳过同名文件。"""
    if not os.path.exists(src):
        return
    if _norm(src) == _norm(dst):
        return
    if not os.path.exists(dst):
        try:
            os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
            os.rename(src, dst)
            return
        except Exception:
            try:
                import shutil
                shutil.move(src, dst)
                return
            except Exception:
                return
    if os.path.isdir(src) and os.path.isdir(dst):
        try:
            for name in os.listdir(src):
                _move_entry(os.path.join(src, name), os.path.join(dst, name))
        except Exception:
            return
        _rmdir_if_empty(src)
        return
    # 目标已有同名文件：保留新位置，尽量清掉旧文件
    try:
        if os.path.isfile(src):
            os.remove(src)
    except Exception:
        pass


def _migrate_legacy_data_once() -> None:
    """把根目录旧数据目录收进 data/。只跑一次。

    旧布局：records/  gallery/  cards/  prompts/  guides/
    新布局：data/（图集去重与偏好同目录；记录卡等文件平铺，带 card_ 等前缀）
    """
    global _MIGRATED
    if _MIGRATED:
        return
    _MIGRATED = True

    root = app_root()
    dest = os.path.join(root, "data")
    try:
        os.makedirs(dest, exist_ok=True)
    except Exception:
        return

    old_records = os.path.join(root, "records")
    if os.path.isdir(old_records) and _norm(old_records) != _norm(dest):
        try:
            for name in os.listdir(old_records):
                _move_entry(os.path.join(old_records, name), os.path.join(dest, name))
        except Exception:
            pass
        _rmdir_if_empty(old_records)

    old_gallery = os.path.join(root, "gallery")
    if os.path.isdir(old_gallery) and _norm(old_gallery) != _norm(dest):
        try:
            for name in os.listdir(old_gallery):
                _move_entry(os.path.join(old_gallery, name), os.path.join(dest, name))
        except Exception:
            pass
        _rmdir_if_empty(old_gallery)

    old_cards = os.path.join(root, "cards")
    new_cards = os.path.join(dest, "cards")
    if os.path.isdir(old_cards) and _norm(old_cards) != _norm(new_cards):
        try:
            os.makedirs(new_cards, exist_ok=True)
            for name in os.listdir(old_cards):
                _move_entry(os.path.join(old_cards, name), os.path.join(new_cards, name))
        except Exception:
            pass
        _rmdir_if_empty(old_cards)

    old_prompts = os.path.join(root, "prompts")
    new_prompts = os.path.join(dest, "prompts")
    if os.path.isdir(old_prompts) and _norm(old_prompts) != _norm(new_prompts):
        try:
            os.makedirs(new_prompts, exist_ok=True)
            for name in os.listdir(old_prompts):
                _move_entry(os.path.join(old_prompts, name), os.path.join(new_prompts, name))
        except Exception:
            pass
        _rmdir_if_empty(old_prompts)

    # 根目录 guides/：已进 assets/guides 的同名图丢掉旧副本；其余当作用户图
    old_guides = os.path.join(root, "guides")
    if os.path.isdir(old_guides):
        bundled = os.path.join(root, "assets", "guides")
        user_guides = os.path.join(dest, "guides")
        try:
            for name in os.listdir(old_guides):
                src = os.path.join(old_guides, name)
                if os.path.isfile(os.path.join(bundled, name)):
                    try:
                        if os.path.isfile(src):
                            os.remove(src)
                    except Exception:
                        pass
                    continue
                try:
                    os.makedirs(user_guides, exist_ok=True)
                except Exception:
                    pass
                _move_entry(src, os.path.join(user_guides, name))
        except Exception:
            pass
        _rmdir_if_empty(old_guides)


def _relocate_index_paths(index_path: str, old_base: str, new_base: str) -> None:
    """index.json 里的托管路径从旧基址改写为新基址。"""
    if not os.path.isfile(index_path):
        return
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return
    old_l = os.path.normcase(os.path.normpath(old_base))

    def _walk(o):
        if isinstance(o, str):
            try:
                p = os.path.normcase(os.path.normpath(o))
            except Exception:
                return o
            if p.startswith(old_l):
                rel = os.path.relpath(o, old_base)
                return os.path.normpath(os.path.join(new_base, rel))
            return o
        if isinstance(o, list):
            return [_walk(x) for x in o]
        if isinstance(o, dict):
            return {k: _walk(v) for k, v in o.items()}
        return o

    try:
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(_walk(data), f, ensure_ascii=False)
    except Exception:
        pass


_GA_DIRNAME = "gameassist"
_GA_LEGACY_DIRNAMES = ("game_assist",)
_GB_DIRNAME = "gamebrain"


def _relocate_user_gameassist_paths(old_base: str, new_base: str) -> None:
    """user.txt 里游戏助手段的托管路径从旧基址改写到新基址。"""
    try:
        p = os.path.join(_data_root_dir(), "user.txt")
        if not os.path.isfile(p):
            return
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return
        changed = False
        for key in ("gameassist", "game_assist"):
            if not isinstance(data.get(key), dict):
                continue
            relocated = _walk_relocate_prefix(data[key], old_base, new_base)
            if relocated != data[key]:
                data[key] = relocated
                changed = True
        if not changed:
            return
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, p)
    except Exception:
        pass


def _migrate_gameassist_once() -> None:
    """把旧 data/game_assist、根目录 game_assist/ 挪到 gameassist/。"""
    global _MIGRATED_GA
    if _MIGRATED_GA:
        return
    _MIGRATED_GA = True
    root = app_root()
    new = os.path.join(root, _GA_DIRNAME)
    olds = []
    for name in _GA_LEGACY_DIRNAMES:
        olds.append(os.path.join(root, "data", name))
        olds.append(os.path.join(root, name))
    for old in olds:
        if not os.path.isdir(old) or _norm(old) == _norm(new):
            continue
        _move_entry(old, new)
        _relocate_index_paths(os.path.join(new, "index.json"), old, new)
        _relocate_user_gameassist_paths(old, new)


def _data_root_dir() -> str:
    """data/ 的绝对路径（不经 ensure_data_layout，避免迁移相互递归）。"""
    return os.path.join(app_root(), "data")


def _records_root_dir() -> str:
    """records/ 的绝对路径（不经 ensure_data_layout，避免迁移相互递归）。"""
    return os.path.join(app_root(), "records")


def _read_user_prefs_early() -> dict:
    """不依赖 user_prefs 模块，直接读 data/user.txt（不存在返回空 dict）。"""
    try:
        p = os.path.join(_data_root_dir(), "user.txt")
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# 公共保存根下的业务子目录（系统总览「公共保存」派生）。
# (prefs 段, 子键或 None, 默认文件夹名, 弹窗里的显示名)
# 实际文件夹名可被 save_subdir_names 覆盖。
PUBLIC_SAVE_SUBDIRS = (
    ("fast_save", None, "WebImageSaver", "速存图文"),
    ("screenshot", None, "ScreenshotImageSaver", "截图工具"),
    ("region_record", None, "RegionRecord", "区域录屏"),
    ("image_proc", None, "ImageProc", "图片处理"),
    ("voice_clone", None, "VoiceClone", "语音克隆"),
    ("video", "douyin", "douyin", "抖音"),
    ("video", "bilibili", "bilibili", "B站"),
    ("video", "youtube", "YouTube", "YouTube"),
    ("video", "other", "Video", "其它视频"),
    ("audio", "qishui", "Audio", "音频下载"),
    ("gallery", None, "e-hentai.org", "e-hentai"),
    ("gallery", "hitomi", "hitomi.la", "hitomi"),
    ("gallery_pixiv", None, "pixiv", "Pixiv"),
)

_SUBDIR_BAD_CHARS = '<>:"/\\|?*'

# Windows 下 Users 里这些名字不是「某个登录用户的主目录」
_SKIP_PROFILE_NAMES = frozenset({
    "public", "default", "default user", "all users",
    "defaultuser0", "wdagutilityaccount",
})


def current_save_host() -> dict:
    """本机 + 当前用户，用来判断 user.txt 里的公共保存路径是不是上一台电脑留下的。"""
    home = (os.environ.get("USERPROFILE") or os.path.expanduser("~") or "")
    home = home.replace("\\", "/").rstrip("/")
    user = (os.environ.get("USERNAME") or os.path.basename(home) or "").strip()
    computer = (
        os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or ""
    ).strip()
    return {"computer": computer, "user": user, "home": home}


def _path_key(path: str) -> str:
    try:
        return os.path.normcase(os.path.normpath(os.path.abspath(os.path.expanduser(path))))
    except Exception:
        return (path or "").replace("\\", "/").lower()


def _is_under_dir(path: str, parent: str) -> bool:
    if not path or not parent:
        return False
    try:
        p = _path_key(path)
        par = _path_key(parent)
    except Exception:
        return False
    return p == par or p.startswith(par + os.sep)


def _current_profile_names() -> set:
    names = set()
    user = (os.environ.get("USERNAME") or "").strip()
    if user:
        names.add(user.lower())
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~") or ""
    base = os.path.basename(str(home).rstrip("\\/"))
    if base:
        names.add(base.lower())
        if "." in base:
            names.add(base.split(".")[0].lower())
    return names


def _windows_users_owner(path: str) -> str:
    """若路径在 <盘符>\\Users\\<name>\\... 下，返回 name，否则空串。"""
    try:
        abs_p = os.path.abspath(os.path.expanduser(path))
    except Exception:
        return ""
    parts = abs_p.replace("/", "\\").split("\\")
    for i, part in enumerate(parts):
        if part.lower() == "users" and i + 1 < len(parts):
            return parts[i + 1]
    return ""


def is_other_user_profile_path(path: str) -> bool:
    """路径落在「别的 Windows 用户」主目录下（换电脑最常见的旧路径形态）。

    不把 Public / Default 算作他人；也不创建目录来做判断。
    """
    p = (path or "").strip()
    if not p:
        return False
    if os.name != "nt":
        home = os.path.expanduser("~")
        try:
            parent = os.path.dirname(os.path.abspath(home))
            if os.path.basename(parent).lower() not in ("home", "users"):
                return False
            rest = os.path.relpath(os.path.abspath(os.path.expanduser(p)), parent)
            owner = rest.split(os.sep)[0] if rest and not rest.startswith("..") else ""
        except Exception:
            return False
        if not owner or owner.lower() in _SKIP_PROFILE_NAMES:
            return False
        return owner.lower() not in _current_profile_names()
    owner = _windows_users_owner(p)
    if not owner or owner.lower() in _SKIP_PROFILE_NAMES:
        return False
    return owner.lower() not in _current_profile_names()


def is_usable_public_save_dir(path: str) -> bool:
    """本机已经存在、且不属于其他用户主目录的公共保存根。不创建目录。"""
    p = (path or "").strip()
    if not p:
        return False
    try:
        if not os.path.isdir(os.path.expanduser(p)):
            return False
    except Exception:
        return False
    if is_other_user_profile_path(p):
        return False
    return True


def _pref_save_path(prefs: dict, section: str, sub_key: str = None) -> str:
    sec = prefs.get(section)
    if not isinstance(sec, dict):
        return ""
    if sub_key:
        sub = sec.get(sub_key)
        if not isinstance(sub, dict):
            return ""
        return (sub.get("save_path") or "").strip()
    return (sec.get("save_path") or "").strip()


def _set_pref_save_path(prefs: dict, section: str, sub_key: str, path: str) -> None:
    sec = prefs.get(section)
    if not isinstance(sec, dict):
        sec = {}
        prefs[section] = sec
    if sub_key:
        sub = sec.get(sub_key)
        if not isinstance(sub, dict):
            sub = {}
            sec[sub_key] = sub
        sub["save_path"] = path
    else:
        sec["save_path"] = path


def subdir_storage_key(section: str, sub_key: str = None) -> str:
    """save_subdir_names 里的键。"""
    return f"{section}.{sub_key}" if sub_key else str(section)


def clean_subdir_name(raw: str) -> str:
    """合法的单层文件夹名。空串表示不合格。"""
    s = (raw or "").strip()
    if not s or s in (".", ".."):
        return ""
    if any(c in s for c in _SUBDIR_BAD_CHARS) or any(ord(c) < 32 for c in s):
        return ""
    if s.endswith(".") or s.endswith(" "):
        return ""
    return s


def resolved_subdir_name(prefs: dict, section: str, sub_key: str, default: str) -> str:
    """用户改过的子目录名优先，否则用出厂名。"""
    names = prefs.get("save_subdir_names") if isinstance(prefs, dict) else None
    if isinstance(names, dict):
        custom = clean_subdir_name(names.get(subdir_storage_key(section, sub_key)))
        if custom:
            return custom
    return default


def guess_public_save_base(prefs: dict) -> str:
    """从各业务 save_path 反推公共根（…/ScreenshotImageSaver 去掉尾巴）。不创建目录。"""
    if not isinstance(prefs, dict):
        return ""
    for row in PUBLIC_SAVE_SUBDIRS:
        section, sub_key, default = row[0], row[1], row[2]
        p = _pref_save_path(prefs, section, sub_key).replace("\\", "/")
        name = resolved_subdir_name(prefs, section, sub_key, default)
        tails = []
        for tail_name in (name, default, "Qishui" if section == "audio" else ""):
            if tail_name and tail_name not in tails:
                tails.append(tail_name)
        for tail_name in tails:
            tail = "/" + tail_name
            if p.endswith(tail):
                return p[: -len(tail)]
    return (prefs.get("save_path_base") or "").strip()


def _host_home_conflict(prefs: dict, base: str) -> bool:
    """user.txt 记录的旧主目录与当前用户不同，且公共根还挂在旧主目录下。"""
    host = prefs.get("save_path_host") if isinstance(prefs, dict) else None
    if not isinstance(host, dict):
        return False
    old_home = (host.get("home") or "").strip()
    cur_home = (current_save_host().get("home") or "").strip()
    if not old_home or not cur_home:
        return False
    if _path_key(old_home) == _path_key(cur_home):
        return False
    return _is_under_dir(base, old_home)


def apply_public_save_base(prefs: dict, base: str, *, mkdir: bool = False) -> dict:
    """把公共根和各业务子目录写入 prefs，并记下本机身份。

    mkdir=False：只改配置，不在磁盘上建目录（换电脑时必须如此，避免造出旧路径）。
    mkdir=True：用户在本机选定目录后，才派生子文件夹。
    """
    if not isinstance(prefs, dict):
        return prefs
    base = (base or "").strip().replace("\\", "/")
    prefs["save_path_base"] = base
    prefs["save_path_host"] = current_save_host()
    for row in PUBLIC_SAVE_SUBDIRS:
        section, sub_key, default = row[0], row[1], row[2]
        subd = resolved_subdir_name(prefs, section, sub_key, default)
        p = os.path.join(base, subd).replace("\\", "/") if base else ""
        _set_pref_save_path(prefs, section, sub_key, p)
        if mkdir and base:
            try:
                os.makedirs(os.path.join(base, subd), exist_ok=True)
            except Exception:
                pass
    return prefs


def this_machine_downloads_dir() -> str:
    """当前用户的下载目录；不存在时仍返回候选路径，不创建。"""
    try:
        d = (user_downloads_dir() or "").strip()
        if d:
            return d
    except Exception:
        pass
    return os.path.expanduser("~/Downloads")


def public_save_is_stale(prefs: dict) -> bool:
    """配置里的公共保存根来自另一台电脑 / 另一个用户，或目录根本不存在。"""
    if not isinstance(prefs, dict):
        return False
    base = (prefs.get("save_path_base") or "").strip()
    if not base:
        base = guess_public_save_base(prefs)
    if base:
        if not is_usable_public_save_dir(base):
            return True
        if _host_home_conflict(prefs, base):
            return True
        return False
    for row in PUBLIC_SAVE_SUBDIRS:
        section, sub_key = row[0], row[1]
        p = _pref_save_path(prefs, section, sub_key)
        if p and not is_usable_public_save_dir(p):
            return True
    return False


def rebase_public_save_prefs(prefs: dict) -> bool:
    """换电脑后：丢掉不可用的旧公共保存路径，改成本机「下载」目录。

    不创建旧路径。返回是否改写了 prefs。
    """
    if not isinstance(prefs, dict):
        return False
    if not public_save_is_stale(prefs):
        host = prefs.get("save_path_host")
        if not (isinstance(host, dict) and (host.get("home") or host.get("computer"))):
            prefs["save_path_host"] = current_save_host()
        return False
    old = (prefs.get("save_path_base") or "").strip() or guess_public_save_base(prefs)
    new_base = this_machine_downloads_dir().replace("\\", "/")
    apply_public_save_base(prefs, new_base, mkdir=False)
    try:
        from utils.logger import get_logger
        get_logger(__name__).info(
            "公共保存目录已改用本机下载目录 old=%s new=%s", old, new_base
        )
    except Exception:
        pass
    return True


def migrate_video_other_subdir(prefs: dict) -> bool:
    """旧版即梦 / LibTV 各自建目录；统一并入公共根下 Video，删掉旧子键。

    其它视频（即梦 / LibTV / XVideos）现共用 video.other.save_path，
    由公共保存根派生 …/Video。返回是否改写了 prefs。
    """
    if not isinstance(prefs, dict):
        return False
    base = (prefs.get("save_path_base") or "").strip()
    if not base:
        base = guess_public_save_base(prefs)
    if not base:
        return False
    sec = prefs.get("video")
    if not isinstance(sec, dict):
        sec = {}
        prefs["video"] = sec
    changed = False
    target = os.path.join(base, "Video").replace("\\", "/")
    sub = sec.get("other")
    if not isinstance(sub, dict):
        sub = {}
        sec["other"] = sub
    if (sub.get("save_path") or "").replace("\\", "/") != target:
        sub["save_path"] = target
        changed = True
    for key in ("jimeng", "liblib"):
        if key in sec:
            del sec[key]
            changed = True
    return changed


def _public_base_dir(prefs: dict = None) -> str:
    """用户配置的「公共保存目录」根。

    只认本机已经存在、且不属于其他用户主目录的路径；
    绝不 os.makedirs 去「补造」旧电脑上的目录。
    解析顺序：save_path_base → 各业务路径反推 → 系统「下载」→ ~/Downloads。
    """
    if prefs is None:
        prefs = _read_user_prefs_early()
    base = (prefs.get("save_path_base") or "").strip()
    if is_usable_public_save_dir(base) and not _host_home_conflict(prefs, base):
        return base
    guessed = guess_public_save_base(prefs)
    if (
        guessed
        and guessed != base
        and is_usable_public_save_dir(guessed)
        and not _host_home_conflict(prefs, guessed)
    ):
        return guessed
    return this_machine_downloads_dir()


def public_save_base(prefs: dict = None) -> str:
    """对外：解析当前机可用的公共保存根（不创建外机目录）。"""
    return _public_base_dir(prefs)


def _walk_relocate_prefix(obj, old_base: str, new_base: str):
    """把 obj 里以 old_base 开头的路径串改写为 new_base 下对应相对路径。"""
    old_l = os.path.normcase(os.path.normpath(old_base))
    if isinstance(obj, str):
        try:
            if os.path.normcase(os.path.normpath(obj)).startswith(old_l + os.sep):
                rel = os.path.relpath(obj, old_base)
                return os.path.normpath(os.path.join(new_base, rel))
        except Exception:
            pass
        return obj
    if isinstance(obj, list):
        return [_walk_relocate_prefix(x, old_base, new_base) for x in obj]
    if isinstance(obj, dict):
        return {
            k: _walk_relocate_prefix(v, old_base, new_base)
            for k, v in obj.items()
        }
    return obj


# 语音克隆迁到公共目录时只搬用户音频；history.jsonl 属程序记录（随后平铺到 data/ 根）。
_VC_AUDIO_EXTS = {
    ".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac", ".wma", ".opus", ".webm",
}


def _migrate_voice_clone_once() -> None:
    """把 data/voice_clone 里的用户音频迁到公共保存根 \\VoiceClone（v9.17+）。

    只搬音频文件；history.jsonl 等程序记录留在 data/voice_clone，
    由 _flatten_data_subdirs_once 随后上提到 data/ 根。
    同时把 user.txt 的 voice_clone 段里指向旧目录的路径改写为新目录。
    新装用户或已在目标目录时直接跳过。
    """
    global _MIGRATED_VC
    if _MIGRATED_VC:
        return
    _MIGRATED_VC = True
    old = os.path.join(_data_root_dir(), "voice_clone")
    new = voice_clone_dir()
    if not os.path.isdir(old) or _norm(new) == _norm(old):
        return
    moved_any = False
    try:
        os.makedirs(new, exist_ok=True)
        for name in os.listdir(old):
            if os.path.splitext(name)[1].lower() not in _VC_AUDIO_EXTS:
                continue
            _move_entry(os.path.join(old, name), os.path.join(new, name))
            moved_any = True
    except Exception:
        return
    if not moved_any:
        return

    # user.txt 的 voice_clone 段改写路径
    try:
        p = os.path.join(_data_root_dir(), "user.txt")
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("voice_clone"), dict):
                vc = _walk_relocate_prefix(data["voice_clone"], old, new)
                if vc != data["voice_clone"]:
                    data["voice_clone"] = vc
                    tmp = p + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    os.replace(tmp, p)
    except Exception:
        pass


def _relocate_subdir_files(sub: str, prefixer) -> None:
    """把 data/<sub> 下的文件上提到 data/ 根并按 prefixer 改名，再删空目录。"""
    d = _data_root_dir()
    sd = os.path.join(d, sub)
    if not os.path.isdir(sd):
        return
    try:
        for name in os.listdir(sd):
            src = os.path.join(sd, name)
            if os.path.isdir(src):
                continue
            new_name = prefixer(name)
            if not new_name:
                continue
            _move_entry(src, os.path.join(d, new_name))
    except Exception:
        pass
    _rmdir_if_empty(sd)


def _prompts_flat_name(name: str) -> str:
    """prompts 子目录文件 → records/data 根固定文件名。"""
    base = os.path.basename(name or "")
    low = base.lower()
    if low == "editor.json":
        return "prompts_editor.json"
    if low == "history.jsonl":
        return "prompts_history.jsonl"
    if base.startswith("prompts_"):
        return base
    return "prompts_" + base


def _relocate_subdir_files_in(root: str, sub: str, prefixer) -> None:
    """把 root/<sub> 下的文件上提到 root 根并按 prefixer 改名，再删空目录。"""
    sd = os.path.join(root, sub)
    if not os.path.isdir(sd):
        return
    try:
        for name in os.listdir(sd):
            src = os.path.join(sd, name)
            if os.path.isdir(src):
                continue
            new_name = prefixer(name)
            if not new_name:
                continue
            _move_entry(src, os.path.join(root, new_name))
    except Exception:
        pass
    _rmdir_if_empty(sd)


def flatten_data_subdirs() -> None:
    """把 data/ 与 records/ 里遗留的业务子目录文件平铺到对应根。

    用户数据平铺进 records/：
      cards    → card_*
      guides   → guide_*
      prompts  → prompts_editor.json / prompts_history.jsonl
      voice_clone → voice_clone_history.jsonl
    同时兼容旧备份仍把这些子目录落在 data/ 的情况：先平铺到 data，再由
    _migrate_user_records_split_once 挪进 records/。
    """
    for root in (_records_root_dir(), _data_root_dir()):
        try:
            os.makedirs(root, exist_ok=True)
        except Exception:
            pass
        _relocate_subdir_files_in(
            root, "cards",
            lambda n: n if n.startswith("card_") else "card_" + n,
        )
        _relocate_subdir_files_in(
            root, "guides",
            lambda n: n if n.startswith("guide_") else "guide_" + n,
        )
        _relocate_subdir_files_in(root, "prompts", _prompts_flat_name)
        src = os.path.join(root, "voice_clone", "history.jsonl")
        dst = os.path.join(root, "voice_clone_history.jsonl")
        if os.path.isfile(src) and not os.path.exists(dst):
            _move_entry(src, dst)
        _rmdir_if_empty(os.path.join(root, "voice_clone"))


def _flatten_data_subdirs_once() -> None:
    """启动时的一次性平铺（后续运行时可用 flatten_data_subdirs() 再触发）。"""
    global _MIGRATED_FLAT
    if _MIGRATED_FLAT:
        return
    _MIGRATED_FLAT = True
    flatten_data_subdirs()



# data/ 根里属于「用户创造」的文件名规则 → 迁入 records/
_USER_RECORD_EXACT = {
    "paste_helper.txt",
    "points_calc.txt",
    "pre_download.json",
    "gallery_eh.txt",
    "gallery_hitomi.txt",
    "prompts_editor.json",
    "prompts_history.jsonl",
    "voice_clone_history.jsonl",
    "world_clock_cities.txt",
}


def _is_user_record_filename(name: str) -> bool:
    """判断 data/ 根下某个文件名是否应归 records/（用户创造的数据）。"""
    if not name or not isinstance(name, str):
        return False
    if name in _USER_RECORD_EXACT:
        return True
    low = name.lower()
    if name.startswith("card_") and low.endswith(".txt"):
        return True
    if name.startswith("guide_"):
        return True
    if name.startswith("prompts_"):
        return True
    return False


def _migrate_user_records_split_once() -> None:
    """把 data/ 里的用户创造数据迁到 records/（只跑一次）。

    data/ 仅保留程序运行数据：user.txt、app.log、crash_*.log 等。
    """
    global _MIGRATED_RECORDS_SPLIT
    if _MIGRATED_RECORDS_SPLIT:
        return
    _MIGRATED_RECORDS_SPLIT = True

    data = _data_root_dir()
    rec = _records_root_dir()
    try:
        os.makedirs(data, exist_ok=True)
        os.makedirs(rec, exist_ok=True)
    except Exception:
        return
    try:
        names = list(os.listdir(data))
    except Exception:
        return
    for name in names:
        if not _is_user_record_filename(name):
            continue
        src = os.path.join(data, name)
        if not os.path.isfile(src):
            continue
        _move_entry(src, os.path.join(rec, name))


def ensure_data_layout() -> str:
    """迁移旧目录并确保 data/、records/、model/ 等存在。启动时尽早调用。

    data/     ：程序运行数据（user.txt、日志…）
    records/  ：用户创造的数据（记录卡、图集已下、粘贴/积分、提示词…）
    """
    _migrate_legacy_data_once()
    _migrate_gameassist_once()
    _migrate_voice_clone_once()
    _migrate_voice_edit_once()
    _flatten_data_subdirs_once()
    _migrate_user_records_split_once()
    d = os.path.join(app_root(), "data")
    r = os.path.join(app_root(), "records")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    try:
        os.makedirs(r, exist_ok=True)
    except Exception:
        pass
    model_dir()
    try:
        voice_edit_dir()
    except Exception:
        pass
    return d


def data_dir() -> str:
    """程序运行数据：偏好 user.txt、日志 app.log / crash_*.log 等。"""
    return ensure_data_layout()


def records_dir() -> str:
    """用户创造的数据目录：记录卡、图集已下、粘贴/积分、提示词、预下载队列等。"""
    ensure_data_layout()
    d = _records_root_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def records_file(*names: str) -> str:
    return os.path.join(records_dir(), *names)


def gallery_dir() -> str:
    """图集已下记录（gallery_eh.txt / gallery_hitomi.txt），放 records/。"""
    return records_dir()


def cards_dir() -> str:
    """速存记录卡：平铺在 records/ 根，文件一律带 card_ 前缀。"""
    return records_dir()


def guides_user_dir() -> str:
    """用户放置功能说明图：平铺在 records/ 根，图片带 guide_ 前缀。"""
    return records_dir()


def guides_bundled_dir() -> str:
    """内置功能说明图：assets/guides/。"""
    return resource_path("assets", "guides")


def kb_data_roots() -> list:
    """知识库目录 [(zip 顶层名, 绝对路径)]：游戏助手 gameassist/、游戏大脑 gamebrain/。

    含托管副本、index.json、last_shot.png。导出 / 重置整目录处理；
    导入时 ZIP 里有才整目录替换，旧备份没有则保留本机知识库。
    """
    return [
        (_GA_DIRNAME, gameassist_dir()),
        (_GB_DIRNAME, gamebrain_dir()),
    ]


def local_data_roots() -> list:
    """导出 / 重置覆盖的本地数据目录（导入按 ZIP 内容分别处理）。

    data/ = 程序运行数据；records/ = 用户创造的数据；
    gameassist/ + gamebrain/ = 知识库（kb_data_roots）。
    导入仍识别旧 zip 里的 gallery/ cards/ 以及「全部塞在 data/」的备份。
    """
    return [
        ("data", data_dir()),
        ("records", records_dir()),
    ] + kb_data_roots()


def tools_dir() -> str:
    """可执行辅助脚本目录（如 OCR 修复 bat），与运行数据分离。"""
    d = os.path.join(app_root(), "tools")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def tools_file(*names: str) -> str:
    return os.path.join(tools_dir(), *names)


def model_dir() -> str:
    """本地大模型目录：项目根 / exe 旁的 model/（权重、配置，不进 data/）。

    faster-whisper-*（语音转写）也放这里。
    不纳入 local_data_roots（体积大，导出/重置不管）。
    """
    d = os.path.join(app_root(), "model")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def model_file(*names: str) -> str:
    return os.path.join(model_dir(), *names)


def _migrate_voice_edit_once() -> None:
    """把误放到公共保存根 / 下载目录的 VoiceEdit 挪到程序根。"""
    global _MIGRATED_VE
    if _MIGRATED_VE:
        return
    _MIGRATED_VE = True
    new = os.path.join(app_root(), "VoiceEdit")
    olds = []
    try:
        olds.append(os.path.join(_public_base_dir(), "VoiceEdit"))
    except Exception:
        pass
    try:
        olds.append(os.path.join(os.path.expanduser("~/Downloads"), "VoiceEdit"))
    except Exception:
        pass
    seen = set()
    for old in olds:
        if not old or not os.path.isdir(old):
            continue
        key = _norm(old)
        if key in seen or key == _norm(new):
            continue
        seen.add(key)
        _move_entry(old, new)
        _rmdir_if_empty(old)


def voice_edit_dir() -> str:
    """语音录入工作目录：录音、上传副本、转写 txt、识别子进程临时文件。

    放程序根目录 VoiceEdit/（同 gameassist/、model/），随程序一起移动。
    """
    d = os.path.join(app_root(), "VoiceEdit")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def voice_clone_dir() -> str:
    """语音克隆用户音频目录：原声样本、裁剪、生成 wav、子进程临时文件。

    用户生成的音频属于「公共保存目录」范畴，不再收进程序私有 data/：
    默认在公共保存根下建 VoiceClone 子目录（与克隆成品同一文件夹）；
    若 user.txt 里 voice_clone.save_path 已指定（可能被用户改过），以它为准。
    """
    prefs = _read_user_prefs_early()
    try:
        saved = ((prefs.get("voice_clone") or {}).get("save_path") or "").strip()
    except Exception:
        saved = ""
    # 旧电脑路径即使写在 user.txt 里也不能 mkdir 造出来，否则会被当成「已存在」接着用
    if saved and is_usable_public_save_dir(saved):
        d = saved
    else:
        d = os.path.join(_public_base_dir(prefs), "VoiceClone")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def voice_clone_history_path() -> str:
    """语音克隆历史记录：records/voice_clone_history.jsonl（用户创造的数据）。

    公共 VoiceClone 目录只放音频；历史记录进 records/。
    """
    return os.path.join(records_dir(), "voice_clone_history.jsonl")


def gameassist_dir() -> str:
    """游戏助手工作目录：知识库文件、索引、最近截图。

    直接放程序根目录（同 model/，不进 data/），便于整体查看；
    随数据管理「导出 / 导入 / 重置」一起走（见 kb_data_roots）。
    """
    d = os.path.join(app_root(), _GA_DIRNAME)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


game_assist_dir = gameassist_dir  # 旧名


def gamebrain_dir() -> str:
    """游戏大脑工作目录：知识库文件、索引、最近截图。

    与游戏助手分开，直接放程序根目录 gamebrain/（同 model/，不进 data/）；
    随数据管理「导出 / 导入 / 重置」一起走（见 kb_data_roots）。
    """
    d = os.path.join(app_root(), _GB_DIRNAME)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def digital_human_dir() -> str:
    """数字人工作目录（功能已暂存 BACK/digital_human，主程序不再调用）。"""
    d = os.path.join(data_dir(), "digital_human")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


# Windows Known Folder：Downloads / Documents（用户可能把它们挪到 D: 等）
_FOLDERID_DOWNLOADS = "374DE290-123F-4565-9164-39C4925E467B"
_FOLDERID_DOCUMENTS = "FDD39AD0-238F-46AF-ADB4-6C85480369C7"


def _win_known_folder(guid_str: str) -> str:
    """SHGetKnownFolderPath；非 Windows 或失败返回空串。"""
    if os.name != "nt":
        return ""
    try:
        import ctypes
        from ctypes import wintypes
        from uuid import UUID

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", ctypes.c_ulong),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8),
            ]

            def __init__(self, uuidstr: str):
                super().__init__()
                u = UUID(uuidstr)
                self.Data1 = u.time_low
                self.Data2 = u.time_mid
                self.Data3 = u.time_hi_version
                for i, b in enumerate(u.bytes[8:16]):
                    self.Data4[i] = b

        shell32 = ctypes.windll.shell32
        ole32 = ctypes.windll.ole32
        fn = shell32.SHGetKnownFolderPath
        fn.argtypes = [
            ctypes.POINTER(GUID),
            ctypes.c_uint32,
            wintypes.HANDLE,
            ctypes.POINTER(ctypes.c_wchar_p),
        ]
        fn.restype = ctypes.HRESULT
        fid = GUID(guid_str)
        p = ctypes.c_wchar_p()
        hr = fn(ctypes.byref(fid), 0, None, ctypes.byref(p))
        if hr != 0 or not p.value:
            return ""
        path = p.value
        try:
            ole32.CoTaskMemFree(p)
        except Exception:
            pass
        return path
    except Exception:
        return ""


def _first_existing_dir(*candidates: str) -> str:
    seen = set()
    for raw in candidates:
        if not raw:
            continue
        p = os.path.normpath(os.path.expanduser(str(raw)))
        if not p:
            continue
        key = os.path.normcase(os.path.abspath(p))
        if key in seen:
            continue
        seen.add(key)
        if os.path.isdir(p):
            return p
    return ""


def user_downloads_dir() -> str:
    """当前用户的「下载」目录（Known Folder，其次 Downloads / 下载）。"""
    known = _win_known_folder(_FOLDERID_DOWNLOADS)
    if known and os.path.isdir(known):
        return known
    home = os.path.expanduser("~")
    up = os.environ.get("USERPROFILE") or os.environ.get("HOME") or home
    found = _first_existing_dir(
        os.path.join(up, "Downloads"),
        os.path.join(up, "下载"),
        os.path.join(home, "Downloads"),
        os.path.join(home, "下载"),
    )
    return found or (known or os.path.join(up or home, "Downloads"))


def user_documents_dir() -> str:
    """当前用户的「文档」目录（Known Folder，其次 Documents / 文档）。"""
    known = _win_known_folder(_FOLDERID_DOCUMENTS)
    if known and os.path.isdir(known):
        return known
    home = os.path.expanduser("~")
    up = os.environ.get("USERPROFILE") or os.environ.get("HOME") or home
    found = _first_existing_dir(
        os.path.join(up, "Documents"),
        os.path.join(up, "文档"),
        os.path.join(home, "Documents"),
        os.path.join(home, "文档"),
    )
    return found or (known or os.path.join(up or home, "Documents"))


def ocr_repair_bat_path() -> str:
    """OCR 一键修复脚本：tools/fix_ocr.bat。

    若仅存在旧路径 records/fix_ocr.bat 或迁入后的 data/fix_ocr.bat，首次访问时自动迁入。
    """
    new_path = tools_file("fix_ocr.bat")
    if os.path.isfile(new_path):
        return new_path
    candidates = [
        os.path.join(data_dir(), "fix_ocr.bat"),
        os.path.join(app_root(), "records", "fix_ocr.bat"),
    ]
    for old_path in candidates:
        if not os.path.isfile(old_path):
            continue
        try:
            import shutil
            shutil.move(old_path, new_path)
            return new_path
        except Exception:
            return old_path
    return new_path
