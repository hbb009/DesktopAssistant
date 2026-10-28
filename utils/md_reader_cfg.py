# -*- coding: utf-8 -*-
"""文件阅读器的文件关联。不依赖 Qt。本机要写 UserChoiceLatest，不能只改 Classes。"""
from __future__ import annotations

import os
import subprocess
from typing import Dict, List, Sequence, Tuple

from utils.photo_viewer_cfg import (
    _latest_progid,
    _notify_shell,
    _progid_of,
    _ps_quote,
    _restore_classes,
    _run_sfta,
    _sfta_script,
    _user_choice,
    app_icon_spec,
    normalize_ext,
    normalize_exts,
    python_launch_prefix,
    pythonw_exe,
    write_progid_icon,
)

# 同一行共用一个开关。显示名沿用扩展名大写。
EXT_GROUPS: Tuple = (
    ("MD / MARKDOWN / MDOWN", (".md", ".markdown", ".mdown")),
    ("DOC / DOCX", (".doc", ".docx")),
    ("PDF", (".pdf",)),
)
DEFAULT_EXTS: Tuple = tuple(ext for _label, exts in EXT_GROUPS for ext in exts)
PROG_ID = "DesktopAssistant.MarkdownReader"
_APP_NAME = "文件阅读器"
# 「设置 › 默认应用」按这个名字找到本程序。显示名仍是「文件阅读器」。
_REG_APP = "DesktopAssistant.FileReader"
_CAP_KEY = r"Software\DesktopAssistant\FileReader\Capabilities"
_SETTINGS_URI = "ms-settings:defaultapps?registeredAppUser=DesktopAssistant.FileReader"
_settings_opened_at = 0.0


def reader_script() -> str:
    from utils.app_paths import app_root
    return os.path.join(app_root(), "MarkdownViewer", "reader.pyw")


def open_command() -> str:
    py = pythonw_exe()
    script = reader_script()
    if not os.path.isfile(py):
        raise FileNotFoundError("找不到 pythonw.exe")
    if not os.path.isfile(script):
        raise FileNotFoundError("找不到文件阅读器")
    return f'{python_launch_prefix(py)} "{script}" "%1"'


def section_from_prefs(prefs: object) -> dict:
    raw = {}
    if isinstance(prefs, dict):
        got = prefs.get("md_reader")
        if isinstance(got, dict):
            raw = got
    exts = normalize_exts(raw.get("exts") if isinstance(raw.get("exts"), list) else [])
    exts = [e for e in exts if e in DEFAULT_EXTS]
    prev_in = raw.get("prev_prog")
    prev: Dict[str, str] = {}
    if isinstance(prev_in, dict):
        for key, val in prev_in.items():
            ext = normalize_ext(key)
            if ext:
                prev[ext] = str(val or "")
    blocked = normalize_exts(raw.get("assoc_blocked") if isinstance(raw.get("assoc_blocked"), list) else [])
    blocked = [e for e in blocked if e in DEFAULT_EXTS]
    return {"exts": exts, "prev_prog": prev, "assoc_blocked": blocked}


def repair_command() -> bool:
    """Rewrite an existing file-reader ProgID command to this machine's Python."""
    import winreg
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROG_ID}")
    except OSError:
        return False
    try:
        _ensure_progid()
        return True
    except Exception:
        return False


def is_ours(ext: str) -> bool:
    ext = normalize_ext(ext)
    if not ext:
        return False
    latest = _latest_progid(ext)
    if latest:
        return latest == PROG_ID
    return _user_choice(ext) == PROG_ID


def _ucpd_blocked(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "ucpd" in text or "blocked changing the default" in text


def _ensure_registered_app() -> None:
    """登记到「默认应用」，系统保护的类型才能在设置里选到本程序。"""
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _CAP_KEY) as key:
        winreg.SetValueEx(key, "ApplicationName", 0, winreg.REG_SZ, _APP_NAME)
        winreg.SetValueEx(key, "ApplicationDescription", 0, winreg.REG_SZ, "桌面助手文件阅读器")
        icon = app_icon_spec()
        if icon:
            winreg.SetValueEx(key, "ApplicationIcon", 0, winreg.REG_SZ, icon)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _CAP_KEY + r"\FileAssociations") as key:
        for ext in DEFAULT_EXTS:
            winreg.SetValueEx(key, ext, 0, winreg.REG_SZ, PROG_ID)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\RegisteredApplications") as key:
        winreg.SetValueEx(key, _REG_APP, 0, winreg.REG_SZ, _CAP_KEY)


def _open_default_apps() -> bool:
    """打开本程序在「默认应用」里的那一页。短时间内只开一次。"""
    global _settings_opened_at
    import os
    import time
    now = time.monotonic()
    if now - _settings_opened_at < 2.0:
        return True
    try:
        os.startfile(_SETTINGS_URI)
    except OSError:
        return False
    _settings_opened_at = now
    return True


def summarize_notes(notes: Sequence[str]) -> str:
    opened: List[str] = []
    blocked: List[str] = []
    other: List[str] = []
    for note in notes:
        text = (note or "").strip()
        if not text:
            continue
        if text.startswith("已打开 "):
            opened.append(text[len("已打开 "):])
        elif text.startswith("受保护 "):
            blocked.append(text[len("受保护 "):])
        else:
            other.append(text)
    if opened:
        other.append(
            "、".join(opened)
            + " 受系统保护，已打开「默认应用」。请在列表里选成「文件阅读器」。"
        )
    if blocked:
        other.append(
            "、".join(blocked)
            + " 受系统保护。请到「设置 › 应用 › 默认应用」里选成「文件阅读器」。"
        )
    return " ".join(other)


def _ensure_progid() -> None:
    import winreg
    root = rf"Software\Classes\{PROG_ID}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, _APP_NAME)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root + r"\Application") as key:
        winreg.SetValueEx(key, "ApplicationName", 0, winreg.REG_SZ, _APP_NAME)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root + r"\shell\open") as key:
        winreg.SetValueEx(key, "FriendlyAppName", 0, winreg.REG_SZ, _APP_NAME)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root + r"\shell\open\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, open_command())
    write_progid_icon(PROG_ID)
    _ensure_registered_app()


def _set_open_with(ext: str, on: bool) -> None:
    import winreg
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


def associate_ext(ext: str, prev_prog: Dict[str, str], interactive: bool = False) -> str:
    """把扩展名指到文件阅读器。返回需要显示的提示，没有则空字符串。

    .doc 这类被系统保护的类型写不进默认项。交互时改为打开「默认应用」。
    """
    ext = normalize_ext(ext)
    if ext not in DEFAULT_EXTS:
        raise ValueError("不支持的扩展名")
    _ensure_progid()
    _remember_previous(ext, prev_prog)
    _restore_classes(ext, PROG_ID)
    _set_open_with(ext, True)
    icon = app_icon_spec()
    try:
        if icon:
            _run_sfta("Set-FTA", ProgId=PROG_ID, Extension=ext, Icon=icon)
        else:
            _run_sfta("Set-FTA", ProgId=PROG_ID, Extension=ext)
    except RuntimeError as exc:
        if not _ucpd_blocked(exc):
            raise
        _ensure_registered_app()
        _notify_shell()
        if interactive and _open_default_apps():
            return f"已打开 {ext}"
        return f"受保护 {ext}"
    _notify_shell()
    if not is_ours(ext):
        return f"{ext} 已登记，但系统没有把它设成默认打开方式"
    return ""


def _set_fta_batch(exts: Sequence[str], icon: str) -> Dict[str, str]:
    """一次 PowerShell 把多个扩展名指到本程序。返回 {扩展名: 错误信息}，成功的不在里面。"""
    script = _sfta_script()
    if not os.path.isfile(script):
        raise FileNotFoundError("找不到文件关联组件")
    icon_arg = f" -Icon {_ps_quote(icon)}" if icon else ""
    lines = [f". {_ps_quote(script)}"]
    for ext in exts:
        lines.append(
            f"try {{ Set-FTA -ProgId {_ps_quote(PROG_ID)} -Extension {_ps_quote(ext)}{icon_arg} "
            f"-ErrorAction Stop; Write-Output ('DA_OK|' + {_ps_quote(ext)}) }} "
            f"catch {{ Write-Output ('DA_ERR|' + {_ps_quote(ext)} + '|' + (($_ | Out-String) -replace '\\s+', ' ')) }}"
        )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "; ".join(lines)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=0x08000000,
        timeout=60,
    )
    errors: Dict[str, str] = {}
    seen = set()
    for line in (completed.stdout or "").splitlines():
        parts = line.strip().split("|", 2)
        if len(parts) >= 2 and parts[0] in ("DA_OK", "DA_ERR"):
            seen.add(parts[1])
            if parts[0] == "DA_ERR":
                errors[parts[1]] = parts[2] if len(parts) > 2 else "设置文件关联失败"
    detail = (completed.stderr or "").strip() or "设置文件关联失败"
    for ext in exts:
        if ext not in seen:
            errors[ext] = detail
    return errors


def associate_exts(exts: Sequence[str], prev_prog: Dict[str, str], interactive: bool = False) -> List[str]:
    """同 associate_ext，但多个扩展名只起一次 PowerShell。返回需要显示的提示。"""
    todo = [normalize_ext(e) for e in exts]
    if any(e not in DEFAULT_EXTS for e in todo):
        raise ValueError("不支持的扩展名")
    if not todo:
        return []
    _ensure_progid()
    for ext in todo:
        _remember_previous(ext, prev_prog)
        _restore_classes(ext, PROG_ID)
        _set_open_with(ext, True)
    errors = _set_fta_batch(todo, app_icon_spec())
    hard = [f"{e}: {m}" for e, m in errors.items() if not _ucpd_blocked(RuntimeError(m))]
    if hard:
        raise RuntimeError(hard[0])
    notes: List[str] = []
    if errors:
        _ensure_registered_app()
        opened = interactive and _open_default_apps()
        for ext in todo:
            if ext in errors:
                notes.append(f"已打开 {ext}" if opened else f"受保护 {ext}")
    _notify_shell()
    for ext in todo:
        if ext not in errors and not is_ours(ext):
            notes.append(f"{ext} 已登记，但系统没有把它设成默认打开方式")
    return notes


def unassociate_ext(ext: str, prev_prog: Dict[str, str], interactive: bool = False) -> None:
    ext = normalize_ext(ext)
    if not ext:
        return
    prev = prev_prog.get(ext, "")
    try:
        if prev and prev != PROG_ID:
            _run_sfta("Set-FTA", ProgId=prev, Extension=ext)
        elif is_ours(ext) or _user_choice(ext) == PROG_ID:
            _run_sfta("Remove-FTA", ProgId=PROG_ID, Extension=ext)
    except RuntimeError as exc:
        if not _ucpd_blocked(exc):
            raise
        _ensure_registered_app()
        if interactive:
            _open_default_apps()
        where = "已打开的「默认应用」" if interactive else "「设置 › 应用 › 默认应用」"
        raise RuntimeError(f"{ext} 受系统保护，这里关不掉。请在{where}里改成别的程序。") from exc
    if prev and prev != PROG_ID:
        _restore_classes(ext, prev)
    elif _progid_of(ext) == PROG_ID:
        _restore_classes(ext, "")
    _set_open_with(ext, False)
    prev_prog.pop(ext, None)
    _notify_shell()


def sync_associations(exts: Sequence[str], prev_prog: Dict[str, str]) -> Tuple[bool, str]:
    """让注册表和开关一致。返回 (是否改过, 提示)。"""
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
    warning = summarize_notes(notes)
    return changed, warning
