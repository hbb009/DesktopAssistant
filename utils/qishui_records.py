"""汽水音乐已下记录。

和其它视频一样：一行一条，命中就跳过。
分段和预下载一样用「# 汽水音乐」，段内再分成视频、音频。
下过音频不会挡住视频。

文件：records/qishui.txt
普通记录在上，----- 下面是带 _2 / (2) 的另存名（不参与跳过）。
"""

from __future__ import annotations

import os
import re

from utils.logger import get_logger

log = get_logger(__name__)

RECORD_KEY = "qishui"
PLAT = "汽水音乐"
KIND_ORDER = ("视频", "音频")

_VIDEO_MARK = "视频（汽水）"
_AUDIO_MARK = "音频（汽水）"
_QUAL_ORDER = (_VIDEO_MARK, _AUDIO_MARK, "未标明")
_VIDEO_EXT = {".mp4", ".ts", ".mkv", ".webm", ".mov", ".m4v"}
_AUDIO_EXT = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg"}
_MEDIA_EXT = _VIDEO_EXT | _AUDIO_EXT
_AUDIO_ALIAS_EXT = (".mp3", ".m4a", ".aac")

_PREFIX_RE = re.compile(r"^\[桌面助手\]\[[^\]]+\](?P<rest>.+)$")
_NUM_TAIL_RE = re.compile(
    r"^(?P<body>.+?)(?:_(?P<n>\d+)|\s+\((?P<n2>\d+)\))$"
)
_PLAT_NAMES = {"汽水音乐", "汽水", "qishui"}


def _records_path() -> str:
    from utils.gallery_records import records_path
    return records_path(RECORD_KEY)


def is_record_sep(line: str) -> bool:
    s = (line or "").strip()
    return bool(s) and set(s) <= {"-"} and len(s) >= 3


def _has_mark(stem: str) -> bool:
    stem = stem or ""
    return (
        stem.endswith("_" + _VIDEO_MARK)
        or stem.endswith("_" + _AUDIO_MARK)
        or stem.startswith("[桌面助手]")
    )


def _split_numbered(stem: str) -> tuple:
    """标题_音频（汽水）_2 → (标题_音频（汽水）, True)。认不出汽水标记的不当另存名。"""
    mnum = _NUM_TAIL_RE.match(stem or "")
    if not mnum or not (mnum.group("n") or mnum.group("n2")):
        return stem, False
    rest = mnum.group("body") or ""
    if _has_mark(rest):
        return rest, True
    return stem, False


def _kind_quality(stem: str, ext: str):
    stem = stem or ""
    ext = (ext or "").lower()
    if stem.endswith("_" + _VIDEO_MARK):
        return "视频", _VIDEO_MARK
    if stem.endswith("_" + _AUDIO_MARK):
        return "音频", _AUDIO_MARK
    if ext in _VIDEO_EXT:
        return "视频", "未标明"
    if ext in _AUDIO_EXT:
        return "音频", "未标明"
    return None


def classify_filename(name: str):
    """文件名 → (平台, 类型, 画质, 是否另存序号名)。对不上返回 None。"""
    base = os.path.basename((name or "").strip())
    if not base:
        return None
    stem, ext = os.path.splitext(base)
    stem, suspect = _split_numbered(stem)
    info = _kind_quality(stem, ext)
    if not info:
        return None
    kind, qual = info
    return PLAT, kind, qual, suspect


def name_aliases(name: str) -> set:
    """同一个作品的文件名。去掉 [桌面助手][标签] 后仍算同一条。

    音频的 .mp3 / .m4a / .aac 互相认，抽出的和直链的不会各下一次。
    """
    n = os.path.basename((name or "").strip())
    if not n:
        return set()
    out = {n}
    m = _PREFIX_RE.match(n)
    rest = (m.group("rest") or "").strip() if m else ""
    if rest:
        out.add(rest)
    stem, ext = os.path.splitext(n)
    info = _kind_quality(stem, ext)
    if info and info[0] == "音频":
        for alt in _AUDIO_ALIAS_EXT:
            out.add(stem + alt)
            if rest:
                rstem = os.path.splitext(rest)[0]
                out.add(rstem + alt)
    return out


def _bare_label(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("#"):
        s = s.lstrip("#").strip()
    if len(s) >= 2 and s.startswith("【") and s.endswith("】"):
        s = s[1:-1].strip()
    if len(s) >= 2 and s.startswith("[") and s.endswith("]") and "·" not in s:
        s = s[1:-1].strip()
    return s


def _bracket_parts(s: str):
    if not (s.startswith("[") and s.endswith("]")):
        return None
    inner = s[1:-1].strip()
    if not inner:
        return None
    parts = [p.strip() for p in inner.split("·")]
    if any(not p for p in parts):
        return None
    return parts


def parse_entries(text: str) -> list:
    """编辑框 / 记录文件 → 条目。同名只留一条；普通记录优先于另存名。"""
    suspect_zone = False
    kind = qual = ""
    by_name = {}
    order = []
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s:
            continue
        if is_record_sep(s):
            suspect_zone = True
            kind = qual = ""
            continue
        bare = _bare_label(s)
        if bare.lower() in _PLAT_NAMES or bare in _PLAT_NAMES:
            kind = qual = ""
            continue
        if bare in KIND_ORDER:
            kind = bare
            qual = bare
            continue
        parts = _bracket_parts(s)
        if parts is not None:
            if parts[0] in KIND_ORDER:
                kind = parts[0]
                qual = parts[1] if len(parts) > 1 else parts[0]
                continue
            if _bare_label(parts[0]).lower() in _PLAT_NAMES:
                kind = qual = ""
                continue
        name = s.split("\t")[-1].strip()
        if not name:
            continue
        inferred = classify_filename(name)
        if inferred:
            _plat, knd, quality, file_suspect = inferred
            suspect = bool(suspect_zone or file_suspect)
        elif kind in KIND_ORDER:
            knd = kind
            quality = qual or "未标明"
            suspect = bool(suspect_zone)
        else:
            continue
        if knd not in KIND_ORDER:
            continue
        entry = {
            "platform": PLAT,
            "kind": knd,
            "quality": quality or "未标明",
            "name": name,
            "suspect": suspect,
        }
        prev = by_name.get(name)
        if prev is None:
            by_name[name] = entry
            order.append(name)
        elif prev.get("suspect") and not suspect:
            by_name[name] = entry
    return [by_name[n] for n in order]


def _sort_key(entry: dict):
    kind = entry.get("kind") or ""
    qual = entry.get("quality") or ""
    try:
        ki = KIND_ORDER.index(kind)
    except ValueError:
        ki = len(KIND_ORDER)
    try:
        qi = _QUAL_ORDER.index(qual)
    except ValueError:
        qi = len(_QUAL_ORDER)
    return (ki, kind, qi, qual, entry.get("name") or "")


def _section_header(kind: str, qual: str) -> str:
    kind = kind or "视频"
    qual = qual or "未标明"
    if not qual or qual == kind or qual == "未标明":
        return "[%s]" % kind
    return "[%s · %s]" % (kind, qual)


def format_entries(entries) -> str:
    """# 汽水音乐 下按视频 / 音频分组。另存名放在 ----- 下面。"""
    normal = [
        e for e in (entries or [])
        if e and not e.get("suspect") and e.get("kind") in KIND_ORDER
    ]
    suspects = [
        e for e in (entries or [])
        if e and e.get("suspect") and e.get("kind") in KIND_ORDER
    ]

    def _body(rows) -> list:
        lines = ["# %s" % PLAT]
        current = None
        for e in sorted(rows, key=_sort_key):
            key = (e.get("kind") or "", e.get("quality") or "")
            if key != current:
                current = key
                lines.append(_section_header(key[0], key[1]))
            name = e.get("name") or ""
            if name:
                lines.append(name)
        return lines

    parts = _body(normal)
    if suspects:
        parts.append("-----")
        parts.extend(_body(suspects))
    return "\n".join(parts) + "\n"


def _read_text(path: str):
    from utils.gallery_records import _decode_record_bytes
    try:
        with open(path, "rb") as f:
            raw = f.read()
        return _decode_record_bytes(raw)
    except Exception:
        log.exception("读取汽水记录失败 path=%s", path)
        return None


def load_entries(path: str = None) -> list:
    path = path or _records_path()
    if not path or not os.path.isfile(path):
        return []
    text = _read_text(path)
    if text is None:
        return []
    return parse_entries(text)


def save_entries(entries, path: str = None) -> bool:
    path = path or _records_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(format_entries(entries))
        os.replace(tmp, path)
        return True
    except Exception:
        log.exception("写入汽水记录失败")
        return False


def active_names(entries=None) -> set:
    rows = entries if entries is not None else load_entries()
    keys = set()
    for e in rows:
        if e.get("suspect"):
            continue
        keys |= name_aliases(e.get("name") or "")
    return keys


def has_record(name: str, entries=None) -> bool:
    aliases = name_aliases(name)
    if not aliases:
        return False
    return bool(aliases & active_names(entries))


def summary(entries=None) -> tuple:
    """(普通条数, {类型: 条数}, 另存名条数)。"""
    rows = entries if entries is not None else load_entries()
    counts = {}
    n = 0
    suspects = 0
    for e in rows:
        kind = e.get("kind") or ""
        if kind not in KIND_ORDER:
            continue
        if e.get("suspect"):
            suspects += 1
            continue
        n += 1
        counts[kind] = counts.get(kind, 0) + 1
    return n, counts, suspects


def hint_text(entries=None, added_names=None, skip_on: bool = True) -> str:
    n, counts, suspects = summary(entries)
    head = f"已有 {n}条"
    if not skip_on:
        head += " · 防重关"
    bits = []
    for kind in KIND_ORDER:
        c = counts.get(kind) or 0
        if c:
            bits.append(f"{kind} {c}")
    if bits:
        head += " · " + " · ".join(bits)
    if suspects:
        head += f" · 待核 {suspects}"
    names = []
    for x in added_names or []:
        s = str(x or "").strip()
        if s:
            names.append(s)
    if names:
        head += "  " + "  ".join(f"添加 {name}" for name in names)
    return head


def _refresh_category(prev: dict, new: dict) -> bool:
    updated = False
    weak = {"", "未标明"}
    for key in ("kind", "quality"):
        nv = (new.get(key) or "").strip()
        ov = (prev.get(key) or "").strip()
        if not nv or nv == ov or (nv in weak and ov not in weak):
            continue
        prev[key] = nv
        updated = True
    return updated


def _merge(existing, incoming) -> tuple:
    by_name = {}
    order = []
    for e in existing or []:
        name = (e.get("name") or "").strip()
        if not name or (e.get("kind") not in KIND_ORDER):
            continue
        if name not in by_name:
            order.append(name)
        by_name[name] = dict(e)
    added = []
    changed = False
    for e in incoming or []:
        name = (e.get("name") or "").strip()
        if not name or (e.get("kind") not in KIND_ORDER):
            continue
        prev = by_name.get(name)
        if prev is None:
            by_name[name] = dict(e)
            order.append(name)
            changed = True
            if not e.get("suspect"):
                added.append(name)
            continue
        if prev.get("suspect") and not e.get("suspect"):
            by_name[name] = dict(e)
            changed = True
            added.append(name)
            continue
        if not e.get("suspect") and not prev.get("suspect") and _refresh_category(prev, e):
            changed = True
    return [by_name[n] for n in order], added, changed


def add_records(recs, path: str = None) -> list:
    incoming = []
    for rec in recs or []:
        if not isinstance(rec, dict):
            continue
        name = (rec.get("name") or "").strip()
        kind = (rec.get("kind") or "").strip()
        if not name or kind not in KIND_ORDER:
            continue
        incoming.append({
            "platform": PLAT,
            "kind": kind,
            "quality": (rec.get("quality") or "未标明").strip() or "未标明",
            "name": name,
            "suspect": bool(rec.get("suspect")),
        })
    if not incoming:
        return []
    path = path or _records_path()
    merged, added, changed = _merge(load_entries(path), incoming)
    if not changed:
        return []
    if not save_entries(merged, path):
        return []
    return added


def scan_dir(folder: str, path: str = None) -> list:
    """扫描保存目录里的视频和音频，按文件名归到视频 / 音频。

    带汽水标记的按标记分；其余按扩展名分。
    带 _2 / (2) 且能看出原标记的，放进待核，不参与跳过。
    返回本次新加入的正式记录文件名。新的另存名在 scan_dir.last_suspects。
    """
    scan_dir.last_suspects = []
    d = (folder or "").strip()
    if not d or not os.path.isdir(d):
        return []
    found = []
    try:
        names = os.listdir(d)
    except OSError:
        return []
    for name in names:
        ext = os.path.splitext(name)[1].lower()
        if ext not in _MEDIA_EXT:
            continue
        full = os.path.join(d, name)
        try:
            if not os.path.isfile(full) or os.path.getsize(full) <= 0:
                continue
        except OSError:
            continue
        info = classify_filename(name)
        if not info:
            continue
        _plat, kind, qual, suspect = info
        found.append({
            "platform": PLAT,
            "kind": kind,
            "quality": qual,
            "name": name,
            "suspect": suspect,
        })
    if not found:
        return []
    path = path or _records_path()
    before = {
        (e.get("name") or "").strip()
        for e in load_entries(path)
        if (e.get("name") or "").strip()
    }
    added = add_records(found, path)
    after = load_entries(path)
    new_suspects = [
        e for e in after
        if e.get("suspect") and (e.get("name") or "").strip() not in before
    ]
    scan_dir.last_suspects = [e.get("name") for e in new_suspects]
    return added


def read_external(path: str):
    p = (path or "").strip().strip('"')
    if p.lower().startswith("file:///"):
        p = p[8:]
    if not p or not os.path.isfile(p):
        return None
    text = _read_text(p)
    if text is None:
        return None
    return parse_entries(text)


def merge_external(existing, incoming) -> tuple:
    return _merge(existing, incoming)
