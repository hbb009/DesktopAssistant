from __future__ import annotations

import os
import re

from utils.logger import get_logger

log = get_logger(__name__)

# pixiv 不做防重复记录（仅 EH 文件夹名 / hitomi zip 名）
_KEYS = ("gallery_eh", "gallery_hitomi")


def _ensure_dir() -> str:
    try:
        from utils.app_paths import gallery_dir
        return gallery_dir()
    except Exception:
        d = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data",
        )
        os.makedirs(d, exist_ok=True)
        return d


def _records_path(key: str) -> str:
    return os.path.join(_ensure_dir(), f"{key}.txt")


def load_records(key: str) -> list:
    path = _records_path(key)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [
                line.strip()
                for line in f
                if line.strip() and not is_record_sep(line)
            ]
    except Exception:
        log.exception(f"读取 {key} 记录失败")
        return []
    return lines


def has_record(key: str, folder_name: str) -> bool:
    name = (folder_name or "").strip()
    if not name:
        return False
    records = load_records(key)
    return name in records


def _ensure_trailing_newline(path: str, f) -> None:
    """如果文件末尾没有换行符，先补一个，防止追加到上一行末尾。"""
    try:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        if size > 0:
            f.seek(size - 1)
            if f.read(1) != "\n":
                f.write("\n")
    except Exception:
        pass


def add_record(key: str, folder_name: str) -> bool:
    """追加一条下载记录；已存在则跳过。新写入返回 True。"""
    name = (folder_name or "").strip()
    if not name:
        return False
    if has_record(key, name):
        return False
    path = _records_path(key)
    try:
        with open(path, "a", encoding="utf-8") as f:
            _ensure_trailing_newline(path, f)
            f.write(name + "\n")
        return True
    except Exception:
        log.exception(f"写入 {key} 记录失败")
        return False


def parse_record_text(text: str) -> list:
    """编辑框文本 → 非空行列表。"""
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


_RECORD_SEP = "-----"
# 程序/资源管理器另存：Title_2、Title_02、Title (2)，可带 .zip
_NUMBERED_LEAF_RE = re.compile(
    r"^(?P<stem>.+?)(?:_(?P<n1>\d+)|\s+\((?P<n2>\d+)\))(?P<ext>\.zip)?$",
    re.I,
)


def is_record_sep(line: str) -> bool:
    s = (line or "").strip()
    return bool(s) and set(s) <= {"-"} and len(s) >= 3


def looks_like_numbered_record(name: str) -> bool:
    """是否像另存序号名（标题_02 / 标题 (2) / 标题_2.zip）。不删，只供人工核对。"""
    s = (name or "").strip()
    if not s or is_record_sep(s):
        return False
    return bool(_NUMBERED_LEAF_RE.match(s))


def split_suspect_record_lines(lines) -> tuple:
    """拆成（普通记录, 可疑序号名）。丢掉旧分隔线，两边都保序。"""
    normal, suspects = [], []
    for ln in lines or []:
        s = str(ln or "").strip()
        if not s or is_record_sep(s):
            continue
        if looks_like_numbered_record(s):
            suspects.append(s)
        else:
            normal.append(s)
    return normal, suspects


def format_records_with_suspects(normal, suspects) -> str:
    parts = list(normal or [])
    if suspects:
        parts.append(_RECORD_SEP)
        parts.extend(suspects)
    return ("\n".join(parts) + "\n") if parts else ""


def dedupe_record_lines(lines) -> tuple:
    """保序去重。返回 (唯一行, 去掉的重复条数)。"""
    seen = set()
    out = []
    removed = 0
    for ln in lines or []:
        s = (ln or "").strip()
        if not s:
            continue
        if s in seen:
            removed += 1
            continue
        seen.add(s)
        out.append(s)
    return out, removed


def save_records(key: str, lines) -> bool:
    """整表覆盖写入记录文件（去空行）。"""
    key = (key or "").strip()
    if not key:
        return False
    path = _records_path(key)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for ln in lines or []:
                s = (ln or "").strip()
                if s:
                    f.write(s + "\n")
        os.replace(tmp, path)
        return True
    except Exception:
        log.exception(f"覆盖写入 {key} 记录失败")
        return False


def scan_dir_records(key: str, save_dir: str, mode: str = "dir") -> set:
    """扫描保存目录中已有的子文件夹或 zip，与记录文件合并去重。

    mode:
      "dir"  — 扫描一级子文件夹名
      "zip"  — 扫描 .zip 文件名
    返回本次新增的记录名集合。
    """
    d = (save_dir or "").strip()
    if not d or not os.path.isdir(d):
        return set()
    existing = set(load_records(key))
    added: set[str] = set()
    try:
        entries = os.listdir(d)
    except OSError:
        return set()
    for name in entries:
        entry = os.path.join(d, name)
        if mode == "dir":
            if os.path.isdir(entry) and name not in existing:
                existing.add(name)
                added.add(name)
        elif mode == "zip":
            if os.path.isfile(entry) and name.lower().endswith(".zip") and name not in existing:
                existing.add(name)
                added.add(name)
    if added:
        try:
            path = _records_path(key)
            with open(path, "a", encoding="utf-8") as f:
                _ensure_trailing_newline(path, f)
                for n in sorted(added):
                    f.write(n + "\n")
        except Exception:
            log.exception(f"写入 {key} 扫描记录失败")
    return added
