"""已下记录总表：userdata/downloaded.txt。

固定段序：
  # e-hentai / # hitomi / # 即梦 / # LibTV / # XVideos
  / # Pornhub / # MissAV / # x.com / # 汽水音乐

图集两段各记文件夹名或 zip 名。其它视频占六个平台段，汽水音乐占自己的段。
段内一行 ----- 只标记这一段里供人工核对的另存名。
每一页只改自己的段，保存时锁住整份文件（downloaded.txt.lock）。

旧的 gallery_eh.txt、gallery_hitomi.txt、other_video.txt、qishui.txt
在启动和导入后并入总表再删除。pre_download.txt 仍是单独的待处理队列。
"""

from __future__ import annotations

import os
import threading

from utils.logger import get_logger

log = get_logger(__name__)

FILE_NAME = "downloaded.txt"

SECTION_ORDER = (
    "e-hentai",
    "hitomi",
    "即梦",
    "LibTV",
    "XVideos",
    "Pornhub",
    "MissAV",
    "x.com",
    "汽水音乐",
)
_SECTION_SET = set(SECTION_ORDER)

GALLERY_SECTION = {
    "gallery_eh": "e-hentai",
    "gallery_hitomi": "hitomi",
}
OTHER_SECTIONS = ("即梦", "LibTV", "XVideos", "Pornhub", "MissAV", "x.com")
QISHUI_SECTION = "汽水音乐"

# (文件名, 没有 # 标题时归入的段；None 表示文件自身带 # 标题)
_LEGACY_FILES = (
    ("gallery_eh.txt", "e-hentai"),
    ("gallery_hitomi.txt", "hitomi"),
    ("other_video.txt", None),
    ("qishui.txt", None),
)

_THREAD_LOCK = threading.Lock()


def _userdata_dir() -> str:
    from utils.app_paths import _userdata_root_dir
    d = _userdata_root_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def downloaded_path() -> str:
    return os.path.join(_userdata_dir(), FILE_NAME)


def is_downloaded_file(path: str) -> bool:
    if not path:
        return False
    try:
        return os.path.normcase(os.path.abspath(path)) == os.path.normcase(
            os.path.abspath(downloaded_path())
        )
    except Exception:
        return False


def _header_name(line: str) -> str:
    s = (line or "").strip()
    if not s.startswith("#"):
        return ""
    name = s[1:].strip()
    return name if name in _SECTION_SET else ""


def _is_sep(line: str) -> bool:
    s = (line or "").strip()
    return bool(s) and set(s) <= {"-"} and len(s) >= 3


def _read_text(path: str, *, missing_ok: bool = False) -> str:
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        if missing_ok:
            return ""
        raise
    from utils.gallery_records import _decode_record_bytes
    text = _decode_record_bytes(raw)
    if raw and not text:
        raise ValueError(f"无法解码已下记录：{path}")
    return text or ""


def _atomic_write(path: str, text: str) -> None:
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def parse_blocks(text: str) -> list:
    """拆成 [(段名, 行列表)]。段内去掉首尾空行，----- 保留在正文里。"""
    blocks = []
    name = None
    body = []

    def flush():
        nonlocal name, body
        if not name:
            name = None
            body = []
            return
        while body and not str(body[0]).strip():
            body.pop(0)
        while body and not str(body[-1]).strip():
            body.pop()
        blocks.append((name, body))
        name = None
        body = []

    for raw in (text or "").splitlines():
        header = _header_name(raw)
        if header:
            flush()
            name = header
            body = []
            continue
        if name:
            body.append(str(raw).rstrip("\r"))
    flush()
    return blocks


def render_blocks(blocks) -> str:
    lines = []
    for name, body in blocks or []:
        if not name:
            continue
        if lines:
            lines.append("")
        lines.append("# " + name)
        for ln in body or []:
            lines.append(str(ln).rstrip("\r"))
    while lines and lines[-1] == "":
        lines.pop()
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


# 同一主人的段保持原来的先后，不能按段名重新归组。
# 其它视频的 ----- 在最后一个普通段末尾，另存名会再出现一次同名 # 标题；
# 若把第二次出现的标题提前，那些另存名会被当成正式记录。
_OWNERS = (
    ("gallery", ("e-hentai", "hitomi")),
    ("other", OTHER_SECTIONS),
    ("qishui", (QISHUI_SECTION,)),
)


def _owner_of(name: str) -> str:
    for key, names in _OWNERS:
        if name in names:
            return key
    return ""


def _order(blocks) -> list:
    buckets = {key: [] for key, _names in _OWNERS}
    extra = []
    for name, body in blocks or []:
        key = _owner_of(name)
        if key:
            buckets[key].append((name, list(body or [])))
        elif name:
            extra.append((name, list(body or [])))
    ordered = []
    for key, names in _OWNERS:
        found = buckets[key]
        if found:
            ordered.extend(found)
        else:
            ordered.extend((name, []) for name in names)
    ordered.extend(extra)
    return ordered


def _legacy_roots() -> list:
    from utils.app_paths import _data_root_dir, _log_root_dir, _records_root_dir, _userdata_root_dir
    found = []
    seen = set()
    for d in (_userdata_root_dir(), _records_root_dir(), _data_root_dir(), _log_root_dir()):
        if not d or not os.path.isdir(d):
            continue
        key = os.path.normcase(os.path.abspath(d))
        if key in seen:
            continue
        seen.add(key)
        found.append(d)
    return found


def _legacy_paths(roots) -> list:
    """扫描旧分区文件。"""
    out = []
    for root in roots:
        for name, forced in _LEGACY_FILES:
            path = os.path.join(root, name)
            if not os.path.isfile(path):
                continue
            out.append((path, forced))
    return out


def _headerless_body(text: str) -> list:
    lines = [str(raw).rstrip("\r") for raw in (text or "").splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _split_sep(lines) -> tuple:
    normal, tail = [], []
    seen_sep = False
    sep = ""
    for ln in lines or []:
        s = str(ln or "").strip()
        if not s:
            continue
        if _is_sep(s):
            seen_sep = True
            sep = s
            continue
        if seen_sep:
            tail.append(s)
        else:
            normal.append(s)
    return normal, sep, tail


def _join_sep(normal, sep, tail) -> list:
    body = list(normal or [])
    if tail:
        body.append(sep or "-----")
        body.extend(tail)
    return body


def _merge_lines(existing, incoming) -> list:
    """把 incoming 去重并进 existing。新的普通行放在 ----- 前面。"""
    if not any(str(ln).strip() for ln in (existing or [])):
        normal, sep, tail = _split_sep(incoming)
        return _join_sep(normal, sep, tail)
    left_n, left_sep, left_tail = _split_sep(existing)
    right_n, right_sep, right_tail = _split_sep(incoming)
    seen = set(left_n) | set(left_tail)
    for ln in right_n:
        if ln not in seen:
            seen.add(ln)
            left_n.append(ln)
    for ln in right_tail:
        if ln not in seen:
            seen.add(ln)
            left_tail.append(ln)
    sep = left_sep or right_sep
    return _join_sep(left_n, sep, left_tail)


def _merge_named(blocks, name: str, incoming) -> list:
    """把若干行并进同名段。还没有这段时追加一块。"""
    incoming = [str(ln).strip() for ln in (incoming or []) if str(ln).strip()]
    if not incoming:
        return list(blocks or [])
    out = []
    merged = False
    for cur, body in blocks or []:
        if cur == name and not merged:
            out.append((cur, _merge_lines(body, incoming)))
            merged = True
        else:
            out.append((cur, list(body or [])))
    if not merged:
        out.append((name, _merge_lines([], incoming)))
    return out


def _dest_has_lines(blocks, names) -> bool:
    wanted = set(names)
    for name, body in blocks or []:
        if name in wanted and any(str(ln).strip() for ln in body or []):
            return True
    return False


def _copy_sections(blocks, path: str, names) -> list:
    """总表里这些段还是空的：按旧文件原样接上，不改行。"""
    wanted = set(names)
    kept = [(name, body) for name, body in blocks or [] if name not in wanted]
    incoming = [
        (name, list(body or []))
        for name, body in parse_blocks(_read_text(path))
        if name in wanted
    ]
    return kept + incoming


def _rebuild_with_parser(blocks, path: str, names, module_name: str) -> list:
    """两边都有内容时，用原来的解析器合并，正式记录优先于另存名。"""
    import importlib
    mod = importlib.import_module(module_name)
    wanted = set(names)
    dest = [(name, body) for name, body in blocks or [] if name in wanted]
    entries = list(mod.parse_entries(render_blocks(dest)))
    entries.extend(mod.parse_entries(_read_text(path)))
    by_name = {}
    order = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        label = (entry.get("name") or "").strip()
        if not label:
            continue
        prev = by_name.get(label)
        if prev is None:
            by_name[label] = entry
            order.append(label)
        elif prev.get("suspect") and not entry.get("suspect"):
            by_name[label] = entry
    rebuilt = mod.format_entries([by_name[label] for label in order])
    kept = [(name, body) for name, body in blocks or [] if name not in wanted]
    incoming = [
        (name, list(body or []))
        for name, body in parse_blocks(rebuilt)
        if name in wanted
    ]
    return kept + incoming


def _absorb_file(blocks, path: str, forced):
    text = _read_text(path)
    parsed = parse_blocks(text)
    if forced:
        known = [item for item in parsed if item[0]]
        if not known:
            return _merge_named(blocks, forced, _headerless_body(text))
        # 旧文件偶尔已经带上自己的 # 标题。
        if all(name == forced for name, _body in known):
            lines = []
            for _name, body in known:
                lines.extend(body)
            return _merge_named(blocks, forced, lines)
        out = blocks
        for name, body in known:
            out = _merge_named(out, name, body)
        return out
    names = {name for name, _body in parsed if name}
    if not names:
        return list(blocks or [])
    if names <= set(OTHER_SECTIONS):
        if _dest_has_lines(blocks, names):
            return _rebuild_with_parser(blocks, path, OTHER_SECTIONS, "utils.other_video_records")
        return _copy_sections(blocks, path, names)
    if names <= {QISHUI_SECTION}:
        if _dest_has_lines(blocks, names):
            return _rebuild_with_parser(blocks, path, (QISHUI_SECTION,), "utils.qishui_records")
        return _copy_sections(blocks, path, names)
    out = list(blocks or [])
    for name, body in parsed:
        if name:
            out = _merge_named(out, name, body)
    return out


def _compose(existing_text: str, legacies) -> str:
    blocks = parse_blocks(existing_text)
    for path, forced in legacies or []:
        blocks = _absorb_file(blocks, path, forced)
    return render_blocks(_order(blocks))


def _locked(mutator, *, path=None):
    """锁内读取并更新总表，读取失败时保留原文件。"""
    path = path or downloaded_path()
    from utils.photo_viewer_cfg import prefs_file_lock
    with _THREAD_LOCK:
        with prefs_file_lock(path):
            current = _read_text(path, missing_ok=True)
            new_text, result = mutator(current)
            if new_text is None:
                new_text = current
            if new_text != current:
                _atomic_write(path, new_text)
            return result


def merge_legacy_download_records(destination, roots) -> bool:
    """合并旧分区。所有源成功读取且总表落盘之后才清理。"""
    destination = os.fspath(destination)
    removed = False
    from utils.photo_viewer_cfg import prefs_file_lock

    with _THREAD_LOCK, prefs_file_lock(destination):
        legacies = _legacy_paths(roots)
        if not legacies:
            return False
        current = _read_text(destination, missing_ok=True)
        merged = _compose(current, legacies)
        if merged != current:
            _atomic_write(destination, merged)
        for path, _forced in legacies:
            try:
                os.remove(path)
                removed = True
            except OSError:
                log.exception("清理已合并的旧记录失败 path=%s", path)
    if removed:
        log.info("已下记录已并入 %s", destination)
    return removed


def migrate_legacy_download_records() -> bool:
    try:
        roots = _legacy_roots()
        if not _legacy_paths(roots):
            return False
        return merge_legacy_download_records(downloaded_path(), roots)
    except (OSError, ValueError):
        log.exception("合并已下记录失败")
        return False


def read_sections_text(names) -> str:
    """读出这些段，拼回带 # 标题的文本。"""
    migrate_legacy_download_records()
    wanted = set(names or [])
    try:
        text = _read_text(downloaded_path(), missing_ok=True)
    except (OSError, ValueError):
        log.exception("读取已下记录失败")
        return ""
    blocks = [(name, body) for name, body in parse_blocks(text) if name in wanted]
    return render_blocks(blocks)


def write_sections_text(names, text: str) -> bool:
    """用 text 里的同名段替换总表中的这些段，其它段不动。"""
    migrate_legacy_download_records()
    wanted = set(names or [])
    try:
        def mutator(current):
            kept = [(name, body) for name, body in parse_blocks(current) if name not in wanted]
            incoming = [(name, body) for name, body in parse_blocks(text or "") if name in wanted]
            return render_blocks(_order(kept + incoming)), True
        _locked(mutator)
        return True
    except Exception:
        log.exception("写入已下记录失败 sections=%s", ",".join(wanted))
        return False


def read_gallery_lines(key: str) -> list:
    section = GALLERY_SECTION.get((key or "").strip())
    if not section:
        return []
    text = read_sections_text((section,))
    lines = []
    for raw in text.splitlines():
        if _header_name(raw):
            continue
        s = raw.strip()
        if s and not _is_sep(s):
            lines.append(s)
    return lines


def write_gallery_lines(key: str, lines) -> bool:
    section = GALLERY_SECTION.get((key or "").strip())
    if not section:
        return False
    body = []
    for ln in lines or []:
        s = str(ln or "").strip()
        if s:
            body.append(s)
    return write_sections_text((section,), render_blocks([(section, body)]))


def append_gallery_names(key: str, names) -> set:
    """追加还没有的图集记录。返回这次新写入的名字。"""
    section = GALLERY_SECTION.get((key or "").strip())
    if not section:
        return set()
    incoming = []
    seen_in = set()
    for name in names or []:
        s = str(name or "").strip()
        if not s or _is_sep(s) or s in seen_in:
            continue
        seen_in.add(s)
        incoming.append(s)
    if not incoming:
        return set()
    migrate_legacy_download_records()
    added = set()
    try:
        def mutator(current):
            blocks = parse_blocks(current)
            bodies = [body for name, body in blocks if name == section]
            flat = []
            for body in bodies:
                flat.extend(body or [])
            have = {str(ln).strip() for ln in flat if str(ln).strip() and not _is_sep(str(ln))}
            fresh = [s for s in incoming if s not in have]
            if not fresh:
                return current, set()
            merged = _merge_lines(flat, fresh)
            others = [(name, body) for name, body in blocks if name != section]
            return render_blocks(_order(others + [(section, merged)])), set(fresh)
        return _locked(mutator) or set()
    except Exception:
        log.exception("追加图集记录失败 key=%s", key)
        return added
