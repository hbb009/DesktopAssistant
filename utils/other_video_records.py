"""其它视频已下记录。

和 gallery_eh / gallery_hitomi 一样：一行一条，命中就跳过。
分类和预下载一样，只留四个平台段：# 即梦 / # LibTV / # XVideos / # Pornhub。
每段里面再按类型和画质分开，只跳过已经下过的那一档。

  即梦     视频（原片 / 1080p / 720p / 480p / 360p …）
  LibTV    成片
  XVideos  HLS、直链（MP4 高 / MP4 低）
  Pornhub  HLS

文件：records/other_video.txt
普通记录在上，----- 下面是带 _2 / (2) 的另存名（不参与跳过）。
"""

from __future__ import annotations

import os
import re

from utils.logger import get_logger

log = get_logger(__name__)

RECORD_KEY = "other_video"

# 和预下载里这四段的标题一致。本页之后只认这四个。
PLAT_ORDER = ("即梦", "LibTV", "XVideos", "Pornhub")
_PLAT_ORDER = PLAT_ORDER
_KIND_ORDER = ("视频", "成片", "HLS", "直链", "未分类")
_QUAL_ORDER = (
    "原片", "成片",
    "2160p", "1440p", "1080p", "720p", "480p", "360p", "240p", "4K",
    "MP4 高", "MP4 低", "MP4高", "MP4低",
)

_PLAT_TAG = {
    "jimeng": "即梦",
    "liblib": "LibTV",
    "xvideos": "XVideos",
    "pornhub": "Pornhub",
}
_PLAT_ALIAS = {name.lower(): name for name in PLAT_ORDER}
_PLAT_ALIAS.update({
    "jimeng": "即梦",
    "liblib": "LibTV",
    "libtv": "LibTV",
})

# 从文件名末尾认画质。长的放前面（「MP4 高」先于单字）。
_QUAL_SUFFIXES = (
    "MP4 高", "MP4 低", "MP4高", "MP4低",
    "原片", "成片",
    "2160p", "1440p", "1080p", "720p", "480p", "360p", "240p", "4K",
)
_PREFIX_RE = re.compile(r"^\[桌面助手\]\[[^\]]+\](?P<rest>.+)$")

_OURS_RE = re.compile(
    r"^\[桌面助手\]\[(?P<tag>[^\]]+)\](?P<body>.*)(?P<ext>\.[A-Za-z0-9]+)$"
)
_NUM_TAIL_RE = re.compile(
    r"^(?P<body>.+?)(?:_(?P<n>\d+)|\s+\((?P<n2>\d+)\))$"
)
_HEIGHT_RE = re.compile(r"_(\d{3,4}p)$", re.I)
_MEDIA_EXT = {".mp4", ".ts", ".mkv", ".webm", ".mov", ".m4v", ".mp3", ".m4a"}


def _records_path() -> str:
    from utils.gallery_records import records_path
    return records_path(RECORD_KEY)


def is_record_sep(line: str) -> bool:
    s = (line or "").strip()
    return bool(s) and set(s) <= {"-"} and len(s) >= 3


def _normalize_plat(text: str) -> str:
    """# 即梦 / 【LibTV】 / jimeng → 四个平台名之一。对不上返回空。"""
    s = (text or "").strip()
    if s.startswith("#"):
        s = s.lstrip("#").strip()
    if s.startswith("【") and s.endswith("】") and len(s) >= 2:
        s = s[1:-1].strip()
    if s.startswith("[") and s.endswith("]") and "·" not in s:
        s = s[1:-1].strip()
    return _PLAT_ALIAS.get(s.lower(), "")


def classify_label(platform_key: str, label: str) -> tuple:
    """解析得到的平台键 + 画质卡标题 → (平台, 类型, 画质)。

    不是即梦 / LibTV / XVideos / Pornhub 时返回空平台，调用方不要写入记录。
    """
    key = (platform_key or "").strip().lower()
    plat = _PLAT_TAG.get(key, "")
    if not plat:
        return "", "", ""
    raw = (label or "").strip()
    quality = (raw.split("·")[0].strip() if raw else "") or "未标明"
    low = raw.lower()
    if key == "liblib":
        return plat, "成片", quality or "成片"
    if key == "xvideos":
        if "直链" in raw or quality.upper().startswith("MP4"):
            return plat, "直链", quality
        if "hls" in low:
            return plat, "HLS", quality
        return plat, "视频", quality
    if key == "pornhub":
        if "直链" in raw or quality.upper().startswith("MP4"):
            return plat, "直链", quality
        return plat, "HLS", quality
    return plat, "视频", quality


def _quality_from_body(body: str) -> str:
    stem = body or ""
    for q in _QUAL_SUFFIXES:
        if stem.endswith("_" + q):
            return q
    m = _HEIGHT_RE.search(stem)
    if m:
        return m.group(1)
    return "未标明"


def _kind_for_tag(tag: str, quality: str) -> str:
    if tag == "LibTV":
        return "成片"
    if tag in ("XVideos", "Pornhub"):
        if (quality or "").upper().startswith("MP4"):
            return "直链"
        if quality and quality != "未标明":
            return "HLS"
        return "视频"
    if tag == "即梦":
        return "视频"
    return "未分类"


def _split_numbered(stem: str, ext: str) -> tuple:
    """标题_原片_2 → (标题_原片, True)。认不出画质的 _2 不当成另存名。"""
    mnum = _NUM_TAIL_RE.match(stem or "")
    if not mnum or not (mnum.group("n") or mnum.group("n2")):
        return stem, False
    rest = mnum.group("body") or ""
    if _OURS_RE.match(rest + ext) or _quality_from_body(rest) != "未标明":
        return rest, True
    return stem, False


def _infer_unprefixed(quality: str):
    """没有 [桌面助手] 前缀的旧文件：靠末尾画质判断平台。认不出返回 None。"""
    q = quality or ""
    if q == "成片":
        return "LibTV", "成片"
    if q.upper().startswith("MP4"):
        return "XVideos", "直链"
    if q == "原片" or q == "4K" or _HEIGHT_RE.search("_" + q):
        return "即梦", "视频"
    return None


def classify_filename(name: str):
    """文件名 → (平台, 类型, 画质, 是否另存序号名)。

    认两类：
      · [桌面助手][即梦]作者_标题_原片.mp4
      · 旧文件名 作者_标题_原片.mp4 / 标题_成片.mp4
    对不上这四个平台时返回 None。
    """
    base = os.path.basename((name or "").strip())
    if not base:
        return None
    stem, ext = os.path.splitext(base)
    stem, suspect = _split_numbered(stem, ext)
    canonical = stem + ext
    m = _OURS_RE.match(canonical)
    if m:
        tag = _normalize_plat(m.group("tag") or "")
        if not tag:
            return None
        quality = _quality_from_body(m.group("body") or "")
        kind = _kind_for_tag(tag, quality)
        return tag, kind, quality, suspect
    quality = _quality_from_body(stem)
    if quality == "未标明":
        return None
    inferred = _infer_unprefixed(quality)
    if not inferred:
        return None
    plat, kind = inferred
    return plat, kind, quality, suspect


def name_aliases(name: str) -> set:
    """同一个作品的新旧文件名。带前缀的和去掉 [桌面助手][平台] 的算同一条。"""
    n = os.path.basename((name or "").strip())
    if not n:
        return set()
    out = {n}
    m = _PREFIX_RE.match(n)
    rest = (m.group("rest") or "").strip() if m else ""
    if rest:
        out.add(rest)
    return out


def _blank_entry() -> dict:
    return {
        "platform": "",
        "kind": "未分类",
        "quality": "未标明",
        "name": "",
        "suspect": False,
    }


def _bracket_parts(s: str):
    """整行是 [a · b · c] 时拆开。文件名不是这种整行，返回 None。"""
    if not (s.startswith("[") and s.endswith("]")):
        return None
    inner = s[1:-1].strip()
    if not inner:
        return None
    parts = [p.strip() for p in inner.split("·")]
    if any(not p for p in parts):
        return None
    return parts


def _take_header(s: str, plat: str, kind: str, qual: str):
    """分段标题。返回 (是否标题, 平台, 类型, 画质)。平台空表示这段不要。"""
    head = _normalize_plat(s)
    if head and not s.startswith("["):
        return True, head, "", ""
    parts = _bracket_parts(s)
    if parts is None:
        return False, plat, kind, qual
    if len(parts) >= 3:
        named = _normalize_plat(parts[0])
        if named:
            return True, named, parts[1], parts[2]
        return True, "", "", ""
    if len(parts) == 1 and _normalize_plat(parts[0]):
        return True, _normalize_plat(parts[0]), "", ""
    if not plat:
        return True, "", "", ""
    if len(parts) == 1:
        return True, plat, parts[0], parts[0]
    return True, plat, parts[0], parts[1]


def parse_entries(text: str) -> list:
    """编辑框 / 记录文件 → 条目。同名只留一条；普通记录优先于另存名。

    分段和预下载一样：# 即梦 / # LibTV / # XVideos / # Pornhub。
    旧的 [即梦 · 视频 · 原片] 也能读。不在这四个平台里的行丢掉。
    """
    suspect_zone = False
    plat = kind = qual = ""
    by_name = {}
    order = []
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s:
            continue
        if is_record_sep(s):
            suspect_zone = True
            plat = kind = qual = ""
            continue
        is_head, plat, kind, qual = _take_header(s, plat, kind, qual)
        if is_head:
            continue
        name = s.split("\t")[-1].strip()
        if not name:
            continue
        # 本页文件名里已经带了平台和画质，整理时以文件名为准，标题写错也会被拉回来。
        inferred = classify_filename(name)
        if inferred:
            platform, knd, quality, file_suspect = inferred
            suspect = bool(suspect_zone or file_suspect)
        elif plat in PLAT_ORDER:
            platform = plat
            knd = kind or "未分类"
            quality = qual or "未标明"
            suspect = bool(suspect_zone)
        else:
            continue
        if platform not in PLAT_ORDER:
            continue
        entry = {
            "platform": platform,
            "kind": knd,
            "quality": quality,
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
    plat = entry.get("platform") or ""
    kind = entry.get("kind") or ""
    qual = entry.get("quality") or ""
    try:
        pi = _PLAT_ORDER.index(plat)
    except ValueError:
        pi = len(_PLAT_ORDER)
    try:
        ki = _KIND_ORDER.index(kind)
    except ValueError:
        ki = len(_KIND_ORDER)
    try:
        qi = _QUAL_ORDER.index(qual)
    except ValueError:
        qi = len(_QUAL_ORDER)
    return (pi, plat, ki, kind, qi, qual, entry.get("name") or "")


def _section_header(kind: str, qual: str) -> str:
    kind = kind or "未分类"
    qual = qual or "未标明"
    if kind == qual:
        return "[%s]" % kind
    return "[%s · %s]" % (kind, qual)


def format_entries(entries) -> str:
    """按预下载的四个平台段写。每段里再按类型 · 画质分组。

    正式记录始终带上四个 # 标题。另存名放在 ----- 下面，只写有内容的段。
    """
    normal = [
        e for e in (entries or [])
        if e and not e.get("suspect") and (e.get("platform") in PLAT_ORDER)
    ]
    suspects = [
        e for e in (entries or [])
        if e and e.get("suspect") and (e.get("platform") in PLAT_ORDER)
    ]

    def _blocks(rows, plats) -> list:
        grouped = {p: [] for p in plats}
        for e in sorted(rows, key=_sort_key):
            plat = e.get("platform")
            if plat in grouped:
                grouped[plat].append(e)
        lines = []
        for plat in plats:
            if lines:
                lines.append("")
            lines.append("# %s" % plat)
            current = None
            for e in grouped.get(plat) or []:
                key = (
                    e.get("kind") or "未分类",
                    e.get("quality") or "未标明",
                )
                if key != current:
                    current = key
                    lines.append(_section_header(key[0], key[1]))
                name = e.get("name") or ""
                if name:
                    lines.append(name)
        return lines

    parts = _blocks(normal, PLAT_ORDER)
    if suspects:
        used = [p for p in PLAT_ORDER if any(e.get("platform") == p for e in suspects)]
        if parts:
            parts.append("-----")
        parts.extend(_blocks(suspects, used))
    return ("\n".join(parts) + "\n") if parts else ""


def _read_text(path: str):
    from utils.gallery_records import _decode_record_bytes
    try:
        with open(path, "rb") as f:
            raw = f.read()
        return _decode_record_bytes(raw)
    except Exception:
        log.exception("读取其它视频记录失败 path=%s", path)
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
        log.exception("写入其它视频记录失败")
        return False


def active_names(entries=None) -> set:
    """参与跳过的文件名（不含 ----- 下的另存名）。

    同时收进去掉 [桌面助手][平台] 之后的旧文件名，新旧两种命名都能对上。
    """
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
    """(普通条数, {平台: 条数}, 另存名条数)。"""
    rows = entries if entries is not None else load_entries()
    counts = {}
    n = 0
    suspects = 0
    for e in rows:
        plat = e.get("platform") or ""
        if plat not in PLAT_ORDER:
            continue
        if e.get("suspect"):
            suspects += 1
            continue
        n += 1
        counts[plat] = counts.get(plat, 0) + 1
    return n, counts, suspects


def hint_text(entries=None, added_names=None, skip_on: bool = True) -> str:
    n, counts, suspects = summary(entries)
    head = f"已有 {n}条"
    if not skip_on:
        head += " · 防重关"
    bits = []
    for plat in PLAT_ORDER:
        c = counts.get(plat) or 0
        if c:
            bits.append(f"{plat} {c}")
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


def _merge(existing, incoming) -> tuple:
    """返回 (合并后的条目, 新正式记录文件名, 是否有写入)。

    同名普通记录不会被另存名盖掉。另存名升成正式记录算新正式记录。
    """
    by_name = {}
    order = []
    for e in existing or []:
        name = (e.get("name") or "").strip()
        if not name:
            continue
        if name not in by_name:
            order.append(name)
        by_name[name] = dict(e)
    added = []
    changed = False
    for e in incoming or []:
        name = (e.get("name") or "").strip()
        if not name:
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


def _refresh_category(prev: dict, new: dict) -> bool:
    """扫描到同一文件时，用新的分类补上「未标明」，不把已经认出的分类降回去。"""
    updated = False
    weak = {
        "platform": {"", "其它"},
        "kind": {"", "未分类"},
        "quality": {"", "未标明"},
    }
    for key, blanks in weak.items():
        nv = (new.get(key) or "").strip()
        ov = (prev.get(key) or "").strip()
        if not nv or nv == ov or (nv in blanks and ov not in blanks):
            continue
        prev[key] = nv
        updated = True
    return updated


def add_records(recs, path: str = None) -> list:
    """写入若干条。已有的普通记录跳过。返回新写入的正式记录文件名。

    带 _2 / (2) 的另存名也会落盘，但放在待核区，不出现在返回值里。
    """
    incoming = []
    for rec in recs or []:
        if not isinstance(rec, dict):
            continue
        name = (rec.get("name") or "").strip()
        if not name:
            continue
        plat = (rec.get("platform") or "").strip()
        if plat not in PLAT_ORDER:
            continue
        incoming.append({
            "platform": plat,
            "kind": (rec.get("kind") or "未分类").strip() or "未分类",
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
    """扫描保存目录，只把即梦 / LibTV / XVideos / Pornhub 并入记录。

    不要求必须带 [桌面助手] 前缀。
    旧的「作者_标题_原片.mp4」「标题_成片.mp4」靠末尾画质归到即梦 / LibTV。
    带 _2 / (2) 且能看出原画质的，放进待核，不参与跳过。
    对不上这四个平台的文件不记。
    返回本次新加入的正式记录文件名（待核不计入）。
    新的另存名放在 scan_dir.last_suspects。
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
        plat, kind, qual, suspect = info
        if plat not in PLAT_ORDER:
            continue
        rec_name = name
        # ffmpeg 没装时成片会留成 .ts，记录仍按计划中的 .mp4 记，下次才能对上。
        if ext == ".ts" and not suspect:
            rec_name = os.path.splitext(name)[0] + ".mp4"
        found.append({
            "platform": plat,
            "kind": kind,
            "quality": qual,
            "name": rec_name,
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
    # 正式记录走返回值；新的另存名只挂在函数上，方便界面说一句「待核」。
    scan_dir.last_suspects = [e.get("name") for e in new_suspects]
    return added


def read_external(path: str):
    """外部 txt → 条目。读失败返回 None。"""
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
