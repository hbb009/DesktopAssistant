# -*- coding: utf-8 -*-
"""看图设置：扩展名、文件夹穿透、当前用户的文件关联。不依赖 Qt。"""
from __future__ import annotations

import ctypes
import glob
import os
import re
import shutil
import subprocess
import sys
import winreg
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# 看图程序能直接打开的类型。界面上的勾选只在这张清单里。
DEFAULT_EXTS: Tuple[str, ...] = (
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp",
    ".tif", ".tiff", ".ico", ".jfif", ".ppm", ".pgm",
)
PROG_ID = "DesktopAssistant.PhotoViewer"
PIERCE_STAY = "stay"
PIERCE_NEXT = "next"

_CFG_CACHE: Dict[str, object] = {"mark": None, "section": None}
# 本进程上次写进 user.txt 的自适应。看图窗口另写时，用它判断要不要跟着改。
_SAVED_AUTO_FIT: Dict[str, object] = {"known": False, "value": True}


def nkey(name: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def normalize_ext(raw: object) -> str:
    s = str(raw or "").strip().lower()
    if not s:
        return ""
    if not s.startswith("."):
        s = "." + s
    body = s[1:]
    if not body or len(s) > 12:
        return ""
    if not all(c.isalnum() for c in body):
        return ""
    return s


def normalize_exts(items: Iterable) -> List[str]:
    out: List[str] = []
    seen = set()
    for it in items or []:
        ext = normalize_ext(it)
        if ext and ext not in seen:
            seen.add(ext)
            out.append(ext)
    return out


def viewer_script() -> str:
    from utils.app_paths import app_root
    return os.path.join(app_root(), "PhotoViewer", "viewer.pyw")


def _python_dirs_from_registry() -> List[str]:
    out: List[str] = []
    roots = (
        (winreg.HKEY_CURRENT_USER, r"Software\Python\PythonCore", 0),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Python\PythonCore", winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Python\PythonCore", winreg.KEY_WOW64_32KEY),
    )
    for hive, root, extra in roots:
        try:
            with winreg.OpenKey(hive, root, 0, winreg.KEY_READ | extra) as key:
                idx = 0
                while True:
                    try:
                        name = winreg.EnumKey(key, idx)
                    except OSError:
                        break
                    idx += 1
                    try:
                        with winreg.OpenKey(
                            key, name + r"\InstallPath", 0, winreg.KEY_READ | extra
                        ) as ip:
                            exe = ""
                            base = ""
                            try:
                                exe, _ = winreg.QueryValueEx(ip, "ExecutablePath")
                            except OSError:
                                pass
                            try:
                                base, _ = winreg.QueryValueEx(ip, "")
                            except OSError:
                                pass
                            if exe:
                                out.append(os.path.dirname(os.path.abspath(exe)))
                            if base:
                                out.append(base)
                    except OSError:
                        pass
        except OSError:
            pass
    return out


def _common_python_dirs() -> List[str]:
    out: List[str] = []
    bases = (
        (os.environ.get("LOCALAPPDATA"), os.path.join("Programs", "Python", "Python3*")),
        (os.environ.get("ProgramFiles"), "Python3*"),
        (os.environ.get("ProgramFiles(x86)"), "Python3*"),
        ("C:\\", "Python3*"),
    )
    for base, pattern in bases:
        if not base:
            continue
        try:
            out.extend(sorted(glob.glob(os.path.join(base, pattern)), reverse=True))
        except Exception:
            pass
    return out


def _first_existing(paths: Iterable[str]) -> str:
    for path in paths:
        if path and os.path.isfile(path):
            return os.path.abspath(path)
    return ""


def pythonw_exe() -> str:
    """Find a windowless Python on this machine (override, current, PATH, registry, common)."""
    override = (os.environ.get("DESKTOP_ASSISTANT_PYTHONW") or "").strip()
    if override and os.path.isfile(override):
        return os.path.abspath(override)
    dirs: List[str] = []
    for exe in (sys.executable, getattr(sys, "_base_executable", "")):
        if exe:
            dirs.append(os.path.dirname(os.path.abspath(exe)))
    for name in ("pythonw.exe", "python.exe"):
        found = shutil.which(name)
        if found:
            dirs.append(os.path.dirname(os.path.abspath(found)))
    dirs.extend(_python_dirs_from_registry())
    dirs.extend(_common_python_dirs())
    found = _first_existing(os.path.join(folder, "pythonw.exe") for folder in dirs)
    if found:
        return found
    for name in ("pyw.exe", "py.exe"):
        found = shutil.which(name)
        if found:
            return os.path.abspath(found)
    return ""


def python_launch_prefix(py: str = "") -> str:
    py = py or pythonw_exe()
    base = os.path.basename(py).lower()
    if base in ("py.exe", "pyw.exe"):
        return f'"{py}" -3'
    return f'"{py}"'


def open_command() -> str:
    py = pythonw_exe()
    script = viewer_script()
    if not os.path.isfile(py):
        raise FileNotFoundError("找不到 pythonw.exe")
    if not os.path.isfile(script):
        raise FileNotFoundError("找不到看图程序")
    return f'{python_launch_prefix(py)} "{script}" "%1"'


def app_icon_path() -> str:
    """资源管理器里关联文件用的图标：软件 star.ico。"""
    from utils.app_paths import is_frozen, resource_path
    ico = resource_path("assets", "star.ico")
    if os.path.isfile(ico):
        return os.path.abspath(ico)
    if is_frozen():
        exe = os.path.abspath(sys.executable or "")
        if exe and os.path.isfile(exe):
            return exe
    return os.path.abspath(ico)


def app_icon_spec() -> str:
    path = app_icon_path()
    if not path:
        return ""
    if " " in path:
        return f'"{path}",0'
    return f"{path},0"


def write_progid_icon(prog_id: str) -> None:
    spec = app_icon_spec()
    if not spec:
        return
    root = rf"Software\Classes\{prog_id}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root + r"\DefaultIcon") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, spec)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root + r"\Application") as key:
        winreg.SetValueEx(key, "ApplicationIcon", 0, winreg.REG_SZ, spec)


def section_from_prefs(prefs: object) -> dict:
    raw = {}
    if isinstance(prefs, dict):
        got = prefs.get("photo_viewer")
        if isinstance(got, dict):
            raw = got
    exts = normalize_exts(raw.get("exts") if isinstance(raw.get("exts"), list) else [])
    exts = [e for e in exts if e in DEFAULT_EXTS]
    pierce = raw.get("pierce")
    if pierce not in (PIERCE_STAY, PIERCE_NEXT):
        pierce = PIERCE_STAY
    lock_scale = bool(raw.get("lock_scale"))
    auto_window = bool(raw.get("auto_window"))
    # 旧配置没有这一项时保持原来的自适应开着，避免一更新就变成 100%。
    auto_fit = bool(raw.get("auto_fit", True))
    prev_in = raw.get("prev_prog")
    prev: Dict[str, str] = {}
    if isinstance(prev_in, dict):
        for key, val in prev_in.items():
            ext = normalize_ext(key)
            if ext:
                prev[ext] = str(val or "")
    return {
        "exts": exts,
        "pierce": pierce,
        "lock_scale": lock_scale,
        "auto_window": auto_window,
        "auto_fit": auto_fit,
        "prev_prog": prev,
    }


def _user_file() -> str:
    from utils.app_paths import userdata_dir
    return os.path.join(userdata_dir(), "user.txt")


def _file_mark(path: str):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)), st.st_size)


def read_saved_section() -> dict:
    """直接读 user.txt 里的 photo_viewer。文件没有这段时按未设置。"""
    import json
    path = _user_file()
    mark = _file_mark(path)
    if mark is not None and _CFG_CACHE.get("mark") == mark and _CFG_CACHE.get("section") is not None:
        return _CFG_CACHE["section"]  # type: ignore[return-value]
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        section = section_from_prefs(data if isinstance(data, dict) else {})
    except Exception:
        # 读失败不进缓存，避免一次占用把自适应记成默认开。
        return section_from_prefs({})
    _CFG_CACHE["mark"] = mark
    _CFG_CACHE["section"] = section
    return section


def read_pierce() -> str:
    pierce = read_saved_section().get("pierce")
    return pierce if pierce in (PIERCE_STAY, PIERCE_NEXT) else PIERCE_STAY


def read_lock_scale() -> bool:
    return bool(read_saved_section().get("lock_scale"))


def read_auto_window() -> bool:
    return bool(read_saved_section().get("auto_window"))


def read_auto_fit() -> bool:
    return bool(read_saved_section().get("auto_fit", True))


def note_saved_auto_fit(on: object) -> None:
    """本进程刚把这个自适应写进 user.txt。之后磁盘若变了，就是看图窗口改的。"""
    _SAVED_AUTO_FIT["known"] = True
    _SAVED_AUTO_FIT["value"] = bool(on)


def external_auto_fit(memory_value: object) -> Optional[bool]:
    """磁盘上的自适应和内存不一样时，若内存仍是本进程上次写下的值，返回磁盘值。

    总览上刚改过、还没落盘的勾选会返回 None，避免被看图窗口的旧值盖掉。
    """
    try:
        disk = bool(read_auto_fit())
    except Exception:
        return None
    mem = bool(memory_value)
    if disk == mem:
        return None
    if _SAVED_AUTO_FIT.get("known") and mem != bool(_SAVED_AUTO_FIT.get("value")):
        return None
    return disk


class _PrefsFileLock:
    """user.txt 的跨进程锁。看图写自适应和主程序存设置共用。"""

    def __init__(self, target_path: str):
        self.lock_path = target_path + ".lock"
        self.fh = None
        self.held = False

    def __enter__(self):
        import msvcrt
        import time
        try:
            self.fh = open(self.lock_path, "a+b")
            if os.path.getsize(self.lock_path) < 1:
                self.fh.write(b"\0")
                self.fh.flush()
            for _ in range(40):
                try:
                    self.fh.seek(0)
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
                    self.held = True
                    return self
                except OSError:
                    time.sleep(0.025)
        except OSError:
            pass
        return self

    def __exit__(self, *_exc):
        if self.fh is None:
            return False
        if self.held:
            import msvcrt
            try:
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        try:
            self.fh.close()
        except OSError:
            pass
        return False


def prefs_file_lock(target_path: str) -> _PrefsFileLock:
    return _PrefsFileLock(target_path)


def _render_auto_fit(raw: str, on: bool):
    """只改 auto_fit 这一个词。改得到就不动文件里的其它内容。"""
    import json
    word = "true" if on else "false"
    matches = list(re.finditer(r'("auto_fit"\s*:\s*)(true|false)', raw, re.I))
    if len(matches) == 1:
        found = matches[0]
        if found.group(2).lower() == word:
            return raw, False
        updated = raw[:found.start(2)] + word + raw[found.end(2):]
        return updated, True
    try:
        data = json.loads(raw)
    except Exception:
        return None, False
    if not isinstance(data, dict):
        return None, False
    sec = data.get("photo_viewer")
    if not isinstance(sec, dict):
        sec = {}
        data["photo_viewer"] = sec
    sec["auto_fit"] = bool(on)
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n", True


def write_auto_fit(on: bool) -> bool:
    """看图里按 / 时，只把 photo_viewer.auto_fit 写回 user.txt。"""
    path = _user_file()
    on = bool(on)
    try:
        with prefs_file_lock(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = f.read()
            except OSError:
                return False
            updated, changed = _render_auto_fit(raw, on)
            if updated is None:
                return False
            if changed:
                tmp = path + ".autofit.tmp"
                with open(tmp, "w", encoding="utf-8", newline="") as f:
                    f.write(updated)
                os.replace(tmp, path)
    except Exception:
        return False
    _CFG_CACHE["mark"] = None
    _CFG_CACHE["section"] = None
    return True


def list_image_names(folder: str, exts: Iterable[str]) -> List[str]:
    want = {normalize_ext(e) for e in exts if normalize_ext(e)}
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    out = []
    for name in names:
        if os.path.splitext(name)[1].lower() not in want:
            continue
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            out.append(name)
    out.sort(key=nkey)
    return out


def neighbor_folder(folder: str, direction: int, exts: Iterable[str]) -> Optional[str]:
    """同一层的下一个（+1）或上一个（-1）含图片的文件夹。没有则 None。"""
    folder = os.path.abspath(folder)
    parent = os.path.dirname(folder)
    if not parent or os.path.normcase(parent) == os.path.normcase(folder):
        return None
    try:
        names = [
            n for n in os.listdir(parent)
            if os.path.isdir(os.path.join(parent, n))
        ]
    except OSError:
        return None
    names.sort(key=nkey)
    sibs = [os.path.join(parent, n) for n in names]
    cur = None
    for i, path in enumerate(sibs):
        if os.path.normcase(path) == os.path.normcase(folder):
            cur = i
            break
    if cur is None:
        return None
    step = 1 if direction > 0 else -1
    i = cur + step
    while 0 <= i < len(sibs):
        if list_image_names(sibs[i], exts):
            return sibs[i]
        i += step
    return None


def _notify_shell() -> None:
    try:
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)
    except Exception:
        pass


def _progid_of(ext: str) -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{ext}") as key:
            val, _ = winreg.QueryValueEx(key, "")
            return str(val or "")
    except OSError:
        return ""


def _user_choice(ext: str) -> str:
    path = rf"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\{ext}\UserChoice"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            val, _ = winreg.QueryValueEx(key, "ProgId")
            return str(val or "")
    except OSError:
        return ""


def _latest_progid(ext: str) -> str:
    """这台系统实际用来决定双击打开谁的记录。只写 Classes 时，双击仍会弹出选择应用。"""
    path = rf"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\{ext}\UserChoiceLatest\ProgId"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            val, _ = winreg.QueryValueEx(key, "ProgId")
            return str(val or "")
    except OSError:
        return ""


def is_ours(ext: str) -> bool:
    ext = normalize_ext(ext)
    if not ext:
        return False
    latest = _latest_progid(ext)
    if latest:
        return latest == PROG_ID
    return _user_choice(ext) == PROG_ID


def _ensure_progid() -> None:
    root = rf"Software\Classes\{PROG_ID}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "看图")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root + r"\Application") as key:
        winreg.SetValueEx(key, "ApplicationName", 0, winreg.REG_SZ, "看图")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root + r"\shell\open") as key:
        winreg.SetValueEx(key, "FriendlyAppName", 0, winreg.REG_SZ, "看图")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root + r"\shell\open\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, open_command())
    write_progid_icon(PROG_ID)


def repair_command() -> bool:
    """Rewrite an existing PhotoViewer ProgID command to this machine's Python."""
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROG_ID}")
    except OSError:
        return False
    try:
        _ensure_progid()
        return True
    except Exception:
        return False


def repair_all_commands() -> None:
    """Refresh PhotoViewer and Markdown reader commands after moving machines."""
    try:
        repair_command()
    except Exception:
        pass
    try:
        from utils.md_reader_cfg import repair_command as repair_md
        repair_md()
    except Exception:
        pass


def _ps_quote(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def _sfta_script() -> str:
    from utils.app_paths import app_root
    return os.path.join(app_root(), "tools", "sfta.ps1")


def _run_sfta(function: str, **kwargs) -> str:
    """写入带校验的用户选择。本机只认 UserChoiceLatest，旧的 Classes 默认值不够。"""
    script = _sfta_script()
    if not os.path.isfile(script):
        raise FileNotFoundError("找不到文件关联组件")
    parts = [f". {_ps_quote(script)}; {function}"]
    for key, val in kwargs.items():
        parts.append(f"-{key} {_ps_quote(val)}")
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", " ".join(parts)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=0x08000000,
        timeout=60,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(detail or "设置文件关联失败")
    return (completed.stdout or "").strip()


def _set_open_with(ext: str, on: bool) -> None:
    path = rf"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\{ext}\OpenWithProgids"
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
            if on:
                winreg.SetValueEx(key, PROG_ID, 0, winreg.REG_NONE, b"")
            else:
                try:
                    winreg.DeleteValue(key, PROG_ID)
                except OSError:
                    pass
    except OSError:
        pass


def _remember_previous(ext: str, prev_prog: Dict[str, str]) -> None:
    if ext in prev_prog:
        return
    current = _latest_progid(ext) or _user_choice(ext) or _progid_of(ext)
    if current != PROG_ID:
        prev_prog[ext] = current


def _restore_classes(ext: str, progid: str) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{ext}") as key:
        if progid:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, progid)
        else:
            try:
                winreg.DeleteValue(key, "")
            except OSError:
                pass


def associate_ext(ext: str, prev_prog: Dict[str, str]) -> str:
    """把扩展名指到看图。返回需要显示的提示，没有则空字符串。"""
    ext = normalize_ext(ext)
    if ext not in DEFAULT_EXTS:
        raise ValueError("不支持的扩展名")
    _ensure_progid()
    _remember_previous(ext, prev_prog)
    _restore_classes(ext, PROG_ID)
    _set_open_with(ext, True)
    icon = app_icon_spec()
    if icon:
        _run_sfta("Set-FTA", ProgId=PROG_ID, Extension=ext, Icon=icon)
    else:
        _run_sfta("Set-FTA", ProgId=PROG_ID, Extension=ext)
    _notify_shell()
    if not is_ours(ext):
        return f"{ext} 已登记，但系统没有把它设成默认打开方式"
    return ""


def unassociate_ext(ext: str, prev_prog: Dict[str, str]) -> None:
    ext = normalize_ext(ext)
    if not ext:
        return
    prev = prev_prog.get(ext, "")
    if prev and prev != PROG_ID:
        _run_sfta("Set-FTA", ProgId=prev, Extension=ext)
        _restore_classes(ext, prev)
    else:
        if is_ours(ext) or _user_choice(ext) == PROG_ID:
            _run_sfta("Remove-FTA", ProgId=PROG_ID, Extension=ext)
        if _progid_of(ext) == PROG_ID:
            _restore_classes(ext, "")
    _set_open_with(ext, False)
    prev_prog.pop(ext, None)
    _notify_shell()


def sync_associations(exts: Sequence[str], prev_prog: Dict[str, str]) -> Tuple[bool, str]:
    """让注册表和勾选一致。返回 (是否改过, 提示)。"""
    want = set(normalize_exts(exts))
    changed = False
    notes: List[str] = []
    refresh_icon = bool(want or any(is_ours(e) for e in DEFAULT_EXTS))
    if refresh_icon:
        _ensure_progid()
    for ext in DEFAULT_EXTS:
        have = is_ours(ext)
        if ext in want and not have:
            notes.append(associate_ext(ext, prev_prog))
            changed = True
        elif have and ext not in want:
            unassociate_ext(ext, prev_prog)
            changed = True
    if refresh_icon and not changed:
        _notify_shell()
    warning = " ".join(n for n in notes if n)
    return changed, warning
