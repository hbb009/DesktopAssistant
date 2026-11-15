from __future__ import annotations

import os
import re

from utils.logger import get_logger

log = get_logger(__name__)

# pixiv 不做防重复记录（仅 EH 文件夹名 / hitomi zip 名）
_KEYS = ("gallery_eh", "gallery_hitomi")


def _records_path(key: str) -> str:
    """总表路径。key 只决定读写哪一段，弹窗点文件名时打开这一份。"""
    del key
    from utils.downloaded_records import downloaded_path
    return downloaded_path()


def records_path(key: str) -> str:
    """已下记录总表 userdata/downloaded.txt。"""
    return _records_path(key)


def _decode_record_bytes(raw: bytes) -> str:
    """按 BOM / 常见编码解开记录文件。"""
    if not raw:
        return ""
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_record_file_lines(path: str):
    """读取外部记录文件：非空、非分隔线的行。

    成功返回 list；读失败返回 None；空文件返回 []。
    """
    p = (path or "").strip().strip('"')
    if p.lower().startswith("file:///"):
        p = p[8:]
    if not p or not os.path.isfile(p):
        return None
    try:
        with open(p, "rb") as f:
            raw = f.read()
        text = _decode_record_bytes(raw)
    except Exception:
        log.exception("读取外部记录文件失败 path=%s", p)
        return None
    return [
        ln.strip()
        for ln in (text or "").splitlines()
        if ln.strip() and not is_record_sep(ln)
    ]


def merge_record_lines(existing, incoming) -> tuple:
    """把 incoming 去重追加到 existing。返回 (合并后的行, 新增行)。"""
    out = []
    seen = set()
    for ln in existing or []:
        s = str(ln or "").strip()
        if not s:
            continue
        if is_record_sep(s):
            out.append(s)
            continue
        if s not in seen:
            seen.add(s)
            out.append(s)
    added = []
    for ln in incoming or []:
        s = str(ln or "").strip()
        if not s or is_record_sep(s):
            continue
        if s not in seen:
            seen.add(s)
            out.append(s)
            added.append(s)
    return out, added


def load_records(key: str) -> list:
    try:
        from utils.downloaded_records import read_gallery_lines
        return read_gallery_lines(key)
    except Exception:
        log.exception(f"读取 {key} 记录失败")
        return []


def has_record(key: str, folder_name: str) -> bool:
    name = (folder_name or "").strip()
    if not name:
        return False
    records = load_records(key)
    return name in records


def add_record(key: str, folder_name: str) -> bool:
    """追加一条下载记录；已存在则跳过。新写入返回 True。"""
    name = (folder_name or "").strip()
    if not name:
        return False
    try:
        from utils.downloaded_records import append_gallery_names
        return name in append_gallery_names(key, [name])
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
    """覆盖这一段的记录（去空行）。其它段不动。"""
    key = (key or "").strip()
    if not key:
        return False
    try:
        from utils.downloaded_records import write_gallery_lines
        return write_gallery_lines(key, lines)
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
    try:
        entries = os.listdir(d)
    except OSError:
        return set()
    found = []
    for name in entries:
        entry = os.path.join(d, name)
        if mode == "dir":
            if os.path.isdir(entry):
                found.append(name)
        elif mode == "zip":
            if os.path.isfile(entry) and name.lower().endswith(".zip"):
                found.append(name)
    try:
        from utils.downloaded_records import append_gallery_names
        return append_gallery_names(key, found)
    except Exception:
        log.exception(f"写入 {key} 扫描记录失败")
        return set()
