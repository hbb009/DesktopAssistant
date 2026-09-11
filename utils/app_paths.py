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
_MIGRATED_FLAT = False


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


def _migrate_game_assist_once() -> None:
    """把旧 data/game_assist 挪到根目录 game_assist/（v9.17 起 game_assist 归根）。"""
    global _MIGRATED_GA
    if _MIGRATED_GA:
        return
    _MIGRATED_GA = True
    old = os.path.join(app_root(), "data", "game_assist")
    new = os.path.join(app_root(), "game_assist")
    if not os.path.isdir(old) or _norm(new) == _norm(old):
        return
    if not os.path.exists(new):
        try:
            os.makedirs(os.path.dirname(new) or ".", exist_ok=True)
            os.rename(old, new)
            moved = True
        except Exception:
            try:
                import shutil
                shutil.move(old, new)
                moved = True
            except Exception:
                return
        if moved:
            _relocate_index_paths(
                os.path.join(new, "index.json"), old, new
            )
        return
    if os.path.isdir(new):
        try:
            for name in os.listdir(old):
                _move_entry(os.path.join(old, name), os.path.join(new, name))
        except Exception:
            return
        _rmdir_if_empty(old)
        _relocate_index_paths(
            os.path.join(new, "index.json"), old, new
        )


def _data_root_dir() -> str:
    """data/ 的绝对路径（不经 ensure_data_layout，避免迁移相互递归）。"""
    return os.path.join(app_root(), "data")


def _read_user_prefs_early() -> dict:
    """不依赖 user_prefs 模块，直接读 data/user.txt（不存在返回空 dict）。"""
    try:
        p = os.path.join(_data_root_dir(), "user.txt")
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _public_base_dir(prefs: dict = None) -> str:
    """用户配置的「公共保存目录」根。

    解析顺序与 ui_main / settings_section 一致：
    save_path_base → 截图目录的上一级（…/ScreenshotImageSaver 去掉尾巴）
    → 系统「下载」目录 → ~/Downloads。返回值保证是已存在目录。
    """
    if prefs is None:
        prefs = _read_user_prefs_early()
    base = (prefs.get("save_path_base") or "").strip()
    if base and os.path.isdir(base):
        return base
    try:
        shot = ((prefs.get("screenshot") or {}).get("save_path") or "").replace("\\", "/")
    except Exception:
        shot = ""
    if shot.endswith("/ScreenshotImageSaver"):
        cand = shot[: -len("/ScreenshotImageSaver")]
        if cand and os.path.isdir(cand):
            return cand
    try:
        d = user_downloads_dir()
        if d:
            return d
    except Exception:
        pass
    return os.path.expanduser("~/Downloads")


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
    """把 data/voice_clone 里的用户音频迁到公共保存根 \\VoiceClone（v9.16.1+）。

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
    """prompts 子目录文件 → data 根固定文件名。"""
    base = os.path.basename(name or "")
    low = base.lower()
    if low == "editor.json":
        return "prompts_editor.json"
    if low == "history.jsonl":
        return "prompts_history.jsonl"
    if base.startswith("prompts_"):
        return base
    return "prompts_" + base


def flatten_data_subdirs() -> None:
    """把 data/ 里遗留的子目录文件平铺到 data 根（可随时重复调用）。

    data/ 只放文件、不再有业务子目录，靠文件名区分：
      cards    → card_*          （记录卡 txt）
      guides   → guide_*         （用户功能说明图）
      prompts  → prompts_editor.json / prompts_history.jsonl
      voice_clone → voice_clone_history.jsonl
    """
    _relocate_subdir_files(
        "cards",
        lambda n: n if n.startswith("card_") else "card_" + n,
    )
    _relocate_subdir_files(
        "guides",
        lambda n: n if n.startswith("guide_") else "guide_" + n,
    )
    _relocate_subdir_files("prompts", _prompts_flat_name)

    # voice_clone：音频已由 _migrate_voice_clone_once 搬到公共目录，history 上提
    d = _data_root_dir()
    src = os.path.join(d, "voice_clone", "history.jsonl")
    dst = os.path.join(d, "voice_clone_history.jsonl")
    if os.path.isfile(src) and not os.path.exists(dst):
        _move_entry(src, dst)
    _rmdir_if_empty(os.path.join(d, "voice_clone"))


def _flatten_data_subdirs_once() -> None:
    """启动时的一次性平铺（后续运行时可用 flatten_data_subdirs() 再触发）。"""
    global _MIGRATED_FLAT
    if _MIGRATED_FLAT:
        return
    _MIGRATED_FLAT = True
    flatten_data_subdirs()


def ensure_data_layout() -> str:
    """迁移旧目录并确保 data/、model/、game_assist/、公共 VoiceClone 存在。启动时尽早调用。"""
    _migrate_legacy_data_once()
    _migrate_game_assist_once()
    _migrate_voice_clone_once()
    _flatten_data_subdirs_once()
    d = os.path.join(app_root(), "data")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    model_dir()
    return d


def data_dir() -> str:
    """全部可写运行数据：偏好、日志、粘贴/积分仓库、图集去重、预下载队列。"""
    return ensure_data_layout()


def records_dir() -> str:
    """兼容旧名：现为 data/。"""
    return data_dir()


def records_file(*names: str) -> str:
    return os.path.join(records_dir(), *names)


def gallery_dir() -> str:
    """图集去重记录（gallery_eh.txt / gallery_hitomi.txt），与偏好同放 data/。"""
    return data_dir()


def cards_dir() -> str:
    """速存「记录模式」记录卡文本目录：平铺在 data/ 根，文件一律带 card_ 前缀。"""
    return data_dir()


def guides_user_dir() -> str:
    """用户放置功能说明图：平铺在 data/ 根，图片带 guide_ 前缀（如 guide_0810.jpg）。"""
    return data_dir()


def guides_bundled_dir() -> str:
    """内置功能说明图：assets/guides/。"""
    return resource_path("assets", "guides")


def local_data_roots() -> list:
    """导出 / 导入 / 重置覆盖的本地数据目录。

    现仅一项 data/。导入仍识别旧 zip 里的 records/ gallery/ cards/。
    """
    return [
        ("data", data_dir()),
    ]


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
    d = saved or os.path.join(_public_base_dir(prefs), "VoiceClone")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def voice_clone_history_path() -> str:
    """语音克隆历史记录 history.jsonl：平铺在 data/ 根，名为 voice_clone_history.jsonl。

    公共 VoiceClone 目录只放音频；克隆的历史记录不跟用户输出混在一起。
    """
    return os.path.join(_data_root_dir(), "voice_clone_history.jsonl")


def game_assist_dir() -> str:
    """游戏助手工作目录：知识库索引、最近截图。

    直接放程序根目录（同 model/，不进 data/），便于整体查看；不随导出/重置走。
    """
    d = os.path.join(app_root(), "game_assist")
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
