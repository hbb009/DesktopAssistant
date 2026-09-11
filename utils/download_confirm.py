# utils/download_confirm.py
# ---------------------------------------------------------------------------
# 下载前「同名」保险：目标已有同名文件时弹窗三选一
#   1. 中止（默认；10 秒倒计时自动选定）
#   2. 改名（调用方：文件加序号 / 图集目录后加序号）
#   3. 覆盖
# ---------------------------------------------------------------------------

from __future__ import annotations

import os
import shutil
from typing import List, Optional, Sequence, Union

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget

ACTION_ABORT = False          # 中止，不下载
ACTION_OVERWRITE = True       # 覆盖已有文件
ACTION_RENAME = "rename"      # 改名（文件加序号 / 目录后加序号）

ConfirmAction = Union[bool, str]

_DEFAULT_COUNTDOWN = 10


def existing_files(paths: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for p in paths or []:
        p = (p or "").strip()
        if not p:
            continue
        try:
            if not os.path.isfile(p):
                continue
        except Exception:
            continue
        key = os.path.normcase(os.path.abspath(p))
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def list_files_with_prefix(folder: str, prefix: str) -> List[str]:
    folder = (folder or "").strip()
    prefix = (prefix or "").strip()
    if not folder or not prefix or not os.path.isdir(folder):
        return []
    out: List[str] = []
    try:
        for name in os.listdir(folder):
            if not name.startswith(prefix):
                continue
            full = os.path.join(folder, name)
            if os.path.isfile(full):
                out.append(full)
    except Exception:
        pass
    return out


def list_files_containing(folder: str, token: str) -> List[str]:
    folder = (folder or "").strip()
    token = (token or "").strip()
    if not folder or not token or not os.path.isdir(folder):
        return []
    out: List[str] = []
    try:
        for name in os.listdir(folder):
            if token not in name:
                continue
            full = os.path.join(folder, name)
            if os.path.isfile(full):
                out.append(full)
    except Exception:
        pass
    return out


def next_numbered_path(path: str) -> str:
    """无扩展名路径（多为目录）后追加 _1 / _2 … 直到不存在。"""
    path = (path or "").rstrip("\\/")
    if not path:
        return path
    if not os.path.exists(path):
        return path
    for i in range(1, 10000):
        cand = f"{path}_{i}"
        if not os.path.exists(cand):
            return cand
    return f"{path}_{os.getpid()}"


def next_numbered_file(path: str) -> str:
    """带扩展名的文件路径：stem 后追加 _1 / _2 …（foo.zip → foo_1.zip）。"""
    path = (path or "").strip()
    if not path:
        return path
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    for i in range(1, 10000):
        cand = f"{root}_{i}{ext}"
        if not os.path.exists(cand):
            return cand
    return f"{root}_{os.getpid()}{ext}"


def next_numbered_dir_title(save_dir: str, safe_leaf: str) -> str:
    """
    在 save_dir 下为 safe_leaf 找可用目录名：leaf → leaf_1 → leaf_2 …
    返回应作为「作品目录名」的字符串（已安全、可直接 join）。
    """
    save_dir = (save_dir or "").strip()
    leaf = (safe_leaf or "").strip() or "gallery"
    if not save_dir:
        return leaf
    base = os.path.join(save_dir, leaf)
    if not os.path.exists(base):
        return leaf
    for i in range(1, 10000):
        cand = f"{leaf}_{i}"
        if not os.path.exists(os.path.join(save_dir, cand)):
            return cand
    return f"{leaf}_{os.getpid()}"


def _emit_dedupe_log(log_emit, msg: str, level: str = "blue") -> None:
    if not log_emit:
        return
    try:
        log_emit(msg, level)
    except TypeError:
        try:
            log_emit(msg)
        except Exception:
            pass
    except Exception:
        pass


def same_name_file_siblings(canonical_path: str) -> List[str]:
    """枚举与规范文件名同「作品名」的已有文件：foo.ext / foo_1.ext / foo_2.ext …"""
    canonical_path = (canonical_path or "").strip()
    if not canonical_path:
        return []
    folder = os.path.dirname(canonical_path) or "."
    stem = os.path.basename(os.path.splitext(canonical_path)[0])
    ext = os.path.splitext(canonical_path)[1]
    if not stem:
        return []
    out: List[str] = []
    try:
        for name in os.listdir(folder):
            full = os.path.join(folder, name)
            if not os.path.isfile(full):
                continue
            if ext:
                if not name.endswith(ext):
                    continue
                name_stem = name[: -len(ext)]
            else:
                name_stem = name
            if name_stem == stem:
                out.append(full)
            elif name_stem.startswith(stem + "_"):
                suffix = name_stem[len(stem) + 1 :]
                if suffix.isdigit():
                    out.append(full)
    except Exception:
        pass
    return out


def dedupe_by_size_after_rename(
    canonical_path: str,
    new_path: str,
    log_emit=None,
) -> str:
    """同名加序号下载完成后：若与任一已有同名文件大小相同 → 真重复，删新文件。

    返回应保留的路径（真重复时返回已有文件；否则返回 new_path）。
    """
    if not new_path or not os.path.isfile(new_path):
        return new_path
    try:
        new_size = os.path.getsize(new_path)
    except Exception:
        return new_path
    if new_size <= 0:
        return new_path

    new_abs = os.path.normcase(os.path.abspath(new_path))
    for sib in same_name_file_siblings(canonical_path):
        try:
            sib_abs = os.path.normcase(os.path.abspath(sib))
        except Exception:
            continue
        if sib_abs == new_abs:
            continue
        try:
            if not os.path.isfile(sib):
                continue
            if os.path.getsize(sib) != new_size:
                continue
        except Exception:
            continue
        try:
            os.remove(new_path)
        except Exception:
            _emit_dedupe_log(
                log_emit,
                f"同名且大小相同（{new_size} B）→ 判定为重复，但删除 "
                f"{os.path.basename(new_path)} 失败，请手动处理",
            )
            return new_path
        _emit_dedupe_log(
            log_emit,
            f"同名且大小相同（{new_size} B）→ 判定为重复，已删除 "
            f"{os.path.basename(new_path)}，保留 {os.path.basename(sib)}",
        )
        return sib

    _emit_dedupe_log(
        log_emit,
        f"同名但大小不同 → 仅作品名重复，已另存为 {os.path.basename(new_path)}",
    )
    return new_path


def same_name_dir_siblings(canonical_dir: str) -> List[str]:
    """枚举同名作品目录：leaf / leaf_1 / leaf_2 …"""
    canonical_dir = (canonical_dir or "").rstrip("\\/")
    if not canonical_dir:
        return []
    parent = os.path.dirname(canonical_dir) or "."
    stem = os.path.basename(canonical_dir)
    if not stem:
        return []
    out: List[str] = []
    try:
        for name in os.listdir(parent):
            full = os.path.join(parent, name)
            if not os.path.isdir(full):
                continue
            if name == stem:
                out.append(full)
            elif name.startswith(stem + "_"):
                suffix = name[len(stem) + 1 :]
                if suffix.isdigit():
                    out.append(full)
    except Exception:
        pass
    return out


def _dir_file_sizes(folder: str) -> dict:
    """相对路径 → 字节数；跳过点文件。空目录返回空 dict。"""
    folder = (folder or "").rstrip("\\/")
    out = {}
    if not folder or not os.path.isdir(folder):
        return out
    try:
        for root, _dirs, files in os.walk(folder):
            for fn in files:
                if fn.startswith("."):
                    continue
                full = os.path.join(root, fn)
                try:
                    rel = os.path.relpath(full, folder).replace("\\", "/")
                    out[rel] = os.path.getsize(full)
                except Exception:
                    continue
    except Exception:
        return {}
    return out


def dedupe_dir_by_size_after_rename(
    canonical_dir: str,
    new_dir: str,
    log_emit=None,
) -> str:
    """另存目录下载完成后：各文件名与大小都相同 → 真重复，删新目录。"""
    new_dir = (new_dir or "").rstrip("\\/")
    if not new_dir or not os.path.isdir(new_dir):
        return new_dir
    new_map = _dir_file_sizes(new_dir)
    if not new_map:
        return new_dir

    new_abs = os.path.normcase(os.path.abspath(new_dir))
    for sib in same_name_dir_siblings(canonical_dir):
        try:
            sib_abs = os.path.normcase(os.path.abspath(sib))
        except Exception:
            continue
        if sib_abs == new_abs:
            continue
        sib_map = _dir_file_sizes(sib)
        if not sib_map or sib_map != new_map:
            continue
        try:
            shutil.rmtree(new_dir)
        except Exception:
            _emit_dedupe_log(
                log_emit,
                f"同名且各文件大小相同 → 判定为重复，但删除目录 "
                f"{os.path.basename(new_dir)} 失败，请手动处理",
            )
            return new_dir
        _emit_dedupe_log(
            log_emit,
            f"同名且各文件大小相同 → 判定为重复，已删除 "
            f"{os.path.basename(new_dir)}，保留 {os.path.basename(sib)}",
        )
        return sib

    _emit_dedupe_log(
        log_emit,
        f"同名但内容不同 → 仅作品名重复，已另存为 {os.path.basename(new_dir)}",
    )
    return new_dir


def confirm_overwrite_or_abort(
    parent: Optional[QWidget],
    existing: Sequence[str],
    *,
    title: str = "发现同名文件",
    max_list: int = 12,
    countdown: int = _DEFAULT_COUNTDOWN,
    rename_hint: str = "目录或文件名后加序号，另存为新位置",
) -> ConfirmAction:
    """下载前同名确认。

    - existing 为空 → 直接返回 ACTION_OVERWRITE（无需弹窗）
    - 弹窗三选项（默认「中止」，countdown 秒后自动中止）：
        「中止」→ ACTION_ABORT
        「改名」→ ACTION_RENAME
        「覆盖」→ ACTION_OVERWRITE
    """
    paths = existing_files(list(existing or []))
    if not paths:
        return ACTION_OVERWRITE

    names = [os.path.basename(p) for p in paths]
    show = names[: max(1, int(max_list))]
    more = len(names) - len(show)
    lines = "\n".join(f"· {n}" for n in show)
    if more > 0:
        lines += f"\n… 另有 {more} 个"

    secs = max(1, int(countdown or _DEFAULT_COUNTDOWN))

    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setMinimumWidth(520)
    try:
        dlg.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
    except Exception:
        pass
    dlg.setWindowModality(Qt.WindowModal)

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(24, 20, 24, 16)
    layout.setSpacing(14)

    header = QLabel(
        f"当前作品在目录中已全部存在同名文件（共 {len(names)} 个；"
        f"序号/文件名相同，内容未必相同）：\n\n"
        f"{lines}\n\n"
        f"请选择处理方式：\n"
        f"· 中止 — 不下载（倒计时结束后默认选定）\n"
        f"· 改名 — {rename_hint}\n"
        f"· 覆盖 — 用新内容覆盖同名文件\n\n"
        f"（若只是下到一半，程序会自动续下缺失项，不会出现此窗。）"
    )
    header.setWordWrap(True)
    header.setTextFormat(Qt.PlainText)
    header.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
    layout.addWidget(header)

    _result = [ACTION_ABORT]
    _countdown = [secs]

    btn_row = QHBoxLayout()
    btn_row.setSpacing(12)

    btn_abort = QPushButton(f"中止（{_countdown[0]} s）")
    btn_rename = QPushButton("改名")
    btn_overwrite = QPushButton("覆盖")

    for btn in (btn_abort, btn_rename, btn_overwrite):
        btn.setMinimumHeight(32)
        btn.setCursor(Qt.PointingHandCursor)

    # 顺序：1 中止 · 2 改名 · 3 覆盖
    btn_row.addWidget(btn_abort)
    btn_row.addWidget(btn_rename)
    btn_row.addWidget(btn_overwrite)
    layout.addLayout(btn_row)

    def _finish(action):
        try:
            _timer.stop()
        except Exception:
            pass
        _result[0] = action
        dlg.accept()

    btn_abort.clicked.connect(lambda: _finish(ACTION_ABORT))
    btn_rename.clicked.connect(lambda: _finish(ACTION_RENAME))
    btn_overwrite.clicked.connect(lambda: _finish(ACTION_OVERWRITE))

    _timer = QTimer(dlg)
    _timer.setInterval(1000)

    def _tick():
        _countdown[0] -= 1
        if _countdown[0] <= 0:
            _timer.stop()
            _finish(ACTION_ABORT)
            return
        btn_abort.setText(f"中止（{_countdown[0]} s）")

    _timer.timeout.connect(_tick)
    _timer.start()

    try:
        from styles.style_all import theme
        _bg = theme["card_bg"]
        _fg = theme["card_fg"]
        _border = theme.get("card_border", "#3a3a3a")
        _btn_bg = theme.get("btn_bg", "#3a3a3a")
        _btn_hover = theme.get("btn_hover", "#4a4a4a")
        _danger = "#b91c1c"
        _danger_hover = "#dc2626"
        css = f"""
            QDialog {{
                background-color: {_bg};
                color: {_fg};
            }}
            QLabel {{
                color: {_fg};
                background: transparent;
            }}
            QPushButton {{
                background-color: {_btn_bg};
                color: {_fg};
                border: 1px solid {_border};
                border-radius: 6px;
                padding: 6px 18px;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: {_btn_hover};
            }}
        """
        dlg.setStyleSheet(css)
        # 默认按钮「中止」用醒目色
        btn_abort.setStyleSheet(
            f"QPushButton{{background-color:{_danger};border-color:{_danger};color:#fff;}}"
            f"QPushButton:hover{{background-color:{_danger_hover};}}"
        )
    except Exception:
        pass

    dlg.exec_()
    return _result[0]
