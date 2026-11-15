# utils/boot_desk.py
# 开机员：常用目录 / 网站存在 user.txt；常用程序的快捷方式复制到
# 程序目录下的 Boot 文件夹。本机启动项只读注册表和启动文件夹，
# 停用/启用写 StartupApproved，不删除启动项。
from __future__ import annotations

import base64
import os
import shutil
import struct
import subprocess
import time
import winreg
from typing import Optional

from utils.logger import get_logger

log = get_logger(__name__)

_SHORTCUT_DIR_NAME = "Boot"
_OLD_SHORTCUT_DIR_NAME = "开机员"
_CREATE_NO_WINDOW = 0x08000000

# StartupApproved 首字节：1 / 3 为停用，其余（含缺失）视为启用。
_DISABLED_FLAGS = frozenset({1, 3})


class BootDeskError(Exception):
    """给界面看的失败原因。"""


def shortcuts_dir() -> str:
    """程序目录下的 Boot 文件夹，里面只放复制出来的快捷方式。"""
    from utils.app_paths import app_root
    root = app_root()
    folder = os.path.join(root, _SHORTCUT_DIR_NAME)
    old = os.path.join(root, _OLD_SHORTCUT_DIR_NAME)
    if not os.path.isdir(folder) and os.path.isdir(old):
        try:
            os.rename(old, folder)
        except OSError:
            log.exception("把开机员文件夹改名为 Boot 失败")
    os.makedirs(folder, exist_ok=True)
    return folder


def normalize_url(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    head = s.split(":", 1)[0].lower()
    if head not in ("http", "https"):
        s = "https://" + s
    return s


def safe_lnk_basename(name: str) -> str:
    """只留文件名。带目录的字符串会丢掉路径，结果仍在 Boot 文件夹内。"""
    base = os.path.basename((name or "").replace("/", "\\")).strip()
    if not base or base in (".", ".."):
        return ""
    if not base.lower().endswith(".lnk"):
        return ""
    return base


def _safe_stem(name: str) -> str:
    bad = '<>:"/\\|?*'
    chars = []
    for ch in (name or "").strip():
        if ch in bad or ord(ch) < 32:
            continue
        chars.append(ch)
    stem = "".join(chars).strip(" .")
    return (stem[:60] or "程序")


def unique_lnk_name(folder: str, stem: str) -> str:
    stem = _safe_stem(stem)
    name = stem + ".lnk"
    if not os.path.exists(os.path.join(folder, name)):
        return name
    for i in range(2, 100):
        name = f"{stem} ({i}).lnk"
        if not os.path.exists(os.path.join(folder, name)):
            return name
    raise BootDeskError("快捷方式太多，请先删掉一些再添加")


def _ps_quote(text: str) -> str:
    return "'" + (text or "").replace("'", "''") + "'"


def _run_powershell(script: str) -> str:
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    try:
        proc = subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive",
                "-EncodedCommand", encoded,
            ],
            capture_output=True,
            creationflags=_CREATE_NO_WINDOW,
            timeout=30,
        )
    except Exception as exc:
        raise BootDeskError("无法运行 PowerShell：%s" % exc) from exc
    out = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
    err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        raise BootDeskError(err or out or "创建快捷方式失败")
    return out


def create_shortcut(lnk_path: str, target: str, workdir: str = "") -> None:
    """用程序自己的路径做快捷方式，图标指向原程序。"""
    work = workdir or os.path.dirname(target)
    icon = target + ",0"
    script = (
        "$sh = New-Object -ComObject WScript.Shell; "
        "$s = $sh.CreateShortcut(%s); "
        "$s.TargetPath = %s; "
        "$s.WorkingDirectory = %s; "
        "$s.IconLocation = %s; "
        "$s.Save()"
    ) % (_ps_quote(lnk_path), _ps_quote(target), _ps_quote(work), _ps_quote(icon))
    _run_powershell(script)


def read_shortcut_target(lnk_path: str) -> str:
    script = (
        "$sh = New-Object -ComObject WScript.Shell; "
        "$s = $sh.CreateShortcut(%s); "
        "Write-Output $s.TargetPath"
    ) % _ps_quote(lnk_path)
    try:
        return _run_powershell(script).splitlines()[-1].strip()
    except Exception:
        log.exception("读取快捷方式目标失败")
        return ""


def install_program(src: str, name: str = "") -> dict:
    """把程序或已有快捷方式复制进 Boot 文件夹。不改原文件。"""
    src = os.path.abspath(os.path.expandvars(src or ""))
    if not os.path.isfile(src):
        raise BootDeskError("找不到这个程序")
    folder = shortcuts_dir()
    ext = os.path.splitext(src)[1].lower()
    stem = (name or "").strip() or os.path.splitext(os.path.basename(src))[0]
    same_folder = os.path.normcase(os.path.dirname(src)) == os.path.normcase(folder)
    created = True
    if ext == ".lnk" and same_folder:
        lnk_name = os.path.basename(src)
        dest = src
        target = read_shortcut_target(dest)
        created = False
    elif ext == ".lnk":
        lnk_name = unique_lnk_name(folder, stem)
        dest = os.path.join(folder, lnk_name)
        shutil.copy2(src, dest)
        target = read_shortcut_target(dest)
    else:
        lnk_name = unique_lnk_name(folder, stem)
        dest = os.path.join(folder, lnk_name)
        create_shortcut(dest, src, os.path.dirname(src))
        target = src
    display = (name or "").strip() or os.path.splitext(lnk_name)[0]
    return {"name": display, "lnk": lnk_name, "target": target or "", "created": created}


def remove_shortcut(lnk_name: str) -> None:
    """只删除 Boot 文件夹里的副本。"""
    base = safe_lnk_basename(lnk_name)
    if not base:
        return
    path = os.path.join(shortcuts_dir(), base)
    if os.path.isfile(path):
        os.remove(path)


def shortcut_path(lnk_name: str) -> str:
    base = safe_lnk_basename(lnk_name)
    if not base:
        return ""
    return os.path.join(shortcuts_dir(), base)


def startup_enabled_from_blob(data: Optional[bytes]) -> bool:
    if not data:
        return True
    try:
        flag = data[0]
    except Exception:
        return True
    return flag not in _DISABLED_FLAGS


def _filetime_now() -> bytes:
    ft = int((time.time() + 11644473600) * 10_000_000)
    return struct.pack("<Q", ft)


def startup_blob(enabled: bool) -> bytes:
    flag = 2 if enabled else 3
    tail = (b"\x00" * 8) if enabled else _filetime_now()
    return bytes([flag, 0, 0, 0]) + tail


def _approved_place(kind: str):
    """返回 (hive, subkey)。不能改的类型返回 (None, '')."""
    run = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
    folder = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"
    if kind == "hkcu_run":
        return winreg.HKEY_CURRENT_USER, run
    if kind in ("hklm_run", "hklm_run32"):
        return winreg.HKEY_LOCAL_MACHINE, run
    if kind == "user_startup":
        return winreg.HKEY_CURRENT_USER, folder
    if kind == "common_startup":
        return winreg.HKEY_LOCAL_MACHINE, folder
    return None, ""


def _read_approved(kind: str, value_name: str) -> bool:
    hive, sub = _approved_place(kind)
    if hive is None or not value_name:
        return True
    try:
        with winreg.OpenKey(hive, sub, 0, winreg.KEY_READ) as key:
            data, typ = winreg.QueryValueEx(key, value_name)
    except OSError:
        return True
    if typ != winreg.REG_BINARY or not isinstance(data, (bytes, bytearray)):
        return True
    return startup_enabled_from_blob(bytes(data))


def set_startup_enabled(kind: str, value_name: str, enabled: bool) -> None:
    hive, sub = _approved_place(kind)
    if hive is None or not value_name:
        raise BootDeskError("这项不能在这里改，请用系统设置")
    try:
        key = winreg.CreateKeyEx(hive, sub, 0, winreg.KEY_SET_VALUE)
    except PermissionError as exc:
        raise BootDeskError("没有权限改这项。请点「系统设置」，或用管理员身份打开桌面助手。") from exc
    except OSError as exc:
        raise BootDeskError("写入启动项失败：%s" % exc) from exc
    try:
        winreg.SetValueEx(key, value_name, 0, winreg.REG_BINARY, startup_blob(enabled))
    except PermissionError as exc:
        raise BootDeskError("没有权限改这项。请点「系统设置」，或用管理员身份打开桌面助手。") from exc
    except OSError as exc:
        raise BootDeskError("写入启动项失败：%s" % exc) from exc
    finally:
        winreg.CloseKey(key)


def user_startup_dir() -> str:
    return os.path.join(
        os.environ.get("APPDATA", ""),
        r"Microsoft\Windows\Start Menu\Programs\Startup",
    )


def common_startup_dir() -> str:
    return os.path.join(
        os.environ.get("PROGRAMDATA", ""),
        r"Microsoft\Windows\Start Menu\Programs\Startup",
    )


def _read_run(hive, subkey: str, source: str, kind: str, out: list) -> None:
    try:
        key = winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ)
    except OSError:
        return
    try:
        count = winreg.QueryInfoKey(key)[1]
        for i in range(count):
            try:
                name, value, _typ = winreg.EnumValue(key, i)
            except OSError:
                continue
            name = str(name or "").strip()
            if not name:
                continue
            command = os.path.expandvars(str(value or "")).strip()
            out.append({
                "name": name,
                "command": command,
                "source": source,
                "kind": kind,
                "key": name,
                "enabled": _read_approved(kind, name),
            })
    finally:
        winreg.CloseKey(key)


def _read_folder(folder: str, source: str, kind: str, out: list) -> None:
    if not folder or not os.path.isdir(folder):
        return
    try:
        names = os.listdir(folder)
    except OSError:
        log.exception("读取启动文件夹失败")
        return
    for name in sorted(names, key=lambda s: s.lower()):
        if name.lower() in ("desktop.ini",):
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        display = os.path.splitext(name)[0] or name
        out.append({
            "name": display,
            "command": path,
            "source": source,
            "kind": kind,
            "key": name,
            "enabled": _read_approved(kind, name),
        })


def list_startup_items() -> list:
    """本机启动项：用户/本机注册表 Run，以及两个启动文件夹。"""
    items = []
    _read_run(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        "用户注册表", "hkcu_run", items,
    )
    _read_run(
        winreg.HKEY_LOCAL_MACHINE,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        "本机注册表", "hklm_run", items,
    )
    _read_run(
        winreg.HKEY_LOCAL_MACHINE,
        r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run",
        "本机注册表(32位)", "hklm_run32", items,
    )
    _read_folder(user_startup_dir(), "我的启动文件夹", "user_startup", items)
    _read_folder(common_startup_dir(), "公共启动文件夹", "common_startup", items)
    return items


def open_startup_settings() -> None:
    os.startfile("ms-settings:startupapps")


def open_user_startup_folder() -> None:
    folder = user_startup_dir()
    os.makedirs(folder, exist_ok=True)
    os.startfile(folder)


def existing_command_path(command: str) -> str:
    """从启动命令里取出已经存在的文件路径。取不到就返回空。"""
    s = (command or "").strip().strip("\x00")
    if not s:
        return ""
    if s.startswith('"'):
        end = s.find('"', 1)
        cand = s[1:end] if end > 1 else ""
    else:
        cand = s.split(" ")[0]
    cand = os.path.expandvars(cand.strip().strip('"'))
    if cand and os.path.exists(cand):
        return cand
    return ""
