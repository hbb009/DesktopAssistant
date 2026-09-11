# utils/game_assist.py
# 游戏助手：攻略知识库索引 / 检索 / OpenAI 兼容大模型 API 对话。不 import Qt。

from __future__ import annotations

import html
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from utils.logger import get_logger

log = get_logger(__name__)

_TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".html", ".htm", ".json", ".csv",
    ".log", ".rst", ".ini", ".cfg", ".yaml", ".yml",
}
_SKIP_DIR_NAMES = {".git", "__pycache__", "node_modules", ".venv", "venv"}
_CHUNK_CHARS = 700
_MAX_FILE_CHARS = 400_000
_MAX_CHUNKS = 4000
_KB_INLINE_LIMIT = 12_000
KB_INLINE_LIMIT = _KB_INLINE_LIMIT
_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]", re.I)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# 知识库分类：内容必须挂到某个分类（分隔线组）后才可能参与回答。
# 内置分类「未分类」收纳旧内容与新增内容，固定不参与回答。
GID_UNCAT = "__uncat__"
UNCAT_TITLE = "未分类"


def game_assist_dir() -> str:
    try:
        from utils.app_paths import game_assist_dir as _d
        return _d()
    except Exception:
        d = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "game_assist",
        )
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        return d


def index_file_path() -> str:
    return os.path.join(game_assist_dir(), "index.json")


def kb_files_dir() -> str:
    """知识库托管目录：用户加入的文件复制到这里统一管理。"""
    d = os.path.join(game_assist_dir(), "kb")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _is_under_kb(path: str) -> bool:
    try:
        return os.path.normcase(os.path.abspath(path)).startswith(
            os.path.normcase(kb_files_dir())
        )
    except Exception:
        return False


def file_stamp(path: str):
    """源文件指纹 (mtime_ns, size)；不存在返回 None。"""
    try:
        st = os.stat(path)
    except Exception:
        return None
    return (st.st_mtime_ns, st.st_size)


def record_source_stamp(rec: dict, path: str) -> dict:
    """把源文件当前指纹记进记录，供「更新」按钮判断。"""
    stamp = file_stamp(path)
    if stamp:
        rec["origin_mtime"] = int(stamp[0])
        rec["origin_size"] = int(stamp[1])
    return rec


def _unique_kb_target(name: str) -> str:
    """在知识库托管目录内生成不冲突的目标路径。"""
    base, ext = os.path.splitext(os.path.basename(name or "") or "file")
    base = base or "file"
    d = kb_files_dir()
    target = os.path.join(d, base + ext)
    if not os.path.exists(target):
        return os.path.normpath(target)
    for i in range(1, 1000):
        cand = os.path.join(d, f"{base}_{i}{ext}")
        if not os.path.exists(cand):
            return os.path.normpath(cand)
    return os.path.normpath(os.path.join(d, f"{base}_{int(time.time())}{ext}"))


def copy_file_to_kb(src: str) -> str:
    """把文件复制进知识库托管目录，返回目标路径；失败返回空串。"""
    src = os.path.normpath(src or "")
    if not src or not os.path.isfile(src):
        return ""
    if _is_under_kb(src):
        return src
    target = _unique_kb_target(src)
    try:
        shutil.copyfile(src, target)
    except Exception:
        log.exception("复制知识库文件失败 src=%s", src)
        return ""
    return target


def kb_target_for(src: str) -> str:
    """文件加入知识库后应落到的托管路径（按原文件名，不自动改名）。"""
    name = os.path.basename(src or "") or "file"
    return os.path.normpath(os.path.join(kb_files_dir(), name))


def overwrite_kb_copy(src: str, target: str) -> bool:
    """用源文件覆盖知识库托管目录内的目标副本；失败返回 False。"""
    src = os.path.normpath(src or "")
    target = os.path.normpath(target or "")
    if not src or not os.path.isfile(src):
        return False
    if not target or not _is_under_kb(target):
        return False
    try:
        shutil.copyfile(src, target)
        return True
    except Exception:
        log.exception("覆盖知识库文件失败 src=%s target=%s", src, target)
        return False


def restore_missing_copies(sources: Sequence[Dict[str, Any]]) -> int:
    """把「托管副本丢失、但源文件还在」的文件来源恢复回原名副本。

    返回成功恢复的数量。自动重建索引前调用，避免“卡片在、内容读不到”。
    """
    n = 0
    for s in sources or []:
        if not isinstance(s, dict) or (s.get("kind") or "file") != "file":
            continue
        path = os.path.normpath(str(s.get("path") or "").strip())
        origin = os.path.normpath(str(s.get("origin") or "").strip())
        if not path or os.path.exists(path):
            continue
        if not origin or origin == path or not os.path.isfile(origin):
            continue
        if not _is_under_kb(path):
            continue
        if overwrite_kb_copy(origin, path):
            n += 1
    return n


def relocate_missing_file_sources(sources: Sequence[Dict[str, Any]]) -> int:
    """把指向旧托管目录（data/game_assist/kb）已失效路径的文件来源，
    归位到当前托管目录 game_assist/kb 下，缺文件时从源文件复制。

    返回改动的数量。解决 v9.17 目录迁移后，user.txt 里残留旧路径
    导致“卡片在、但永远读不到内容”的问题。
    """
    n = 0
    kb = kb_files_dir()
    used = {
        os.path.normcase(os.path.normpath(str(s.get("path") or "")))
        for s in (sources or [])
    }
    for s in sources or []:
        if not isinstance(s, dict) or (s.get("kind") or "file") != "file":
            continue
        path = os.path.normcase(os.path.normpath(str(s.get("path") or "")))
        if not path or os.path.exists(path):
            continue
        origin = str(s.get("origin") or "").strip()
        name = os.path.basename(path) or os.path.basename(origin) or ""
        if not name:
            continue
        cand = os.path.normpath(os.path.join(kb, name))
        if os.path.normcase(cand) in used and not os.path.isfile(cand):
            continue
        if os.path.isfile(cand):
            s["path"] = os.path.normpath(cand)
            n += 1
            continue
        if not origin or not os.path.isfile(origin):
            continue
        if overwrite_kb_copy(origin, cand):
            s["path"] = os.path.normpath(cand)
            n += 1
    return n


def remove_kb_copy(path: str) -> bool:
    """删除知识库托管目录内的副本（不影响源文件）。

    只在路径确实位于 kb 托管目录内时才删除，防止误删用户的原始文件。
    """
    path = os.path.normpath(path or "")
    if not path or not _is_under_kb(path):
        return False
    try:
        os.remove(path)
        return True
    except Exception:
        log.exception("删除知识库副本失败 path=%s", path)
        return False


def ensure_managed_file_source(rec: dict) -> dict:
    """老版本文件来源（无 origin）复制进知识库目录并记录源文件；处理不了则原样返回。"""
    if not isinstance(rec, dict) or (rec.get("kind") or "file") != "file":
        return rec
    if rec.get("origin"):
        return rec
    path = os.path.normpath(str(rec.get("path") or "").strip())
    if not path or not os.path.isfile(path) or _is_under_kb(path):
        return rec
    target = copy_file_to_kb(path)
    if not target or os.path.normcase(target) == os.path.normcase(path):
        return rec
    out = dict(rec)
    out["path"] = target
    out["origin"] = path
    record_source_stamp(out, path)
    out["updated_at"] = time.time()
    return out


def source_update_state(rec: dict) -> str:
    """文件来源的更新状态：'none' 无源（不显示按钮）/ 'ok' 无需更新 / 'outdated' 可更新。"""
    if not isinstance(rec, dict) or (rec.get("kind") or "file") != "file":
        return "none"
    origin = str(rec.get("origin") or "").strip()
    if not origin:
        return "none"
    cur = file_stamp(origin)
    if cur is None:
        return "none"
    try:
        old = (int(rec.get("origin_mtime")), int(rec.get("origin_size")))
    except (TypeError, ValueError):
        return "outdated"
    if old != cur:
        return "outdated"
    return "ok"


def apply_source_update(rec: dict) -> dict:
    """把源文件同步到知识库副本并刷新指纹；失败返回原记录。"""
    rec = dict(rec or {})
    origin = str(rec.get("origin") or "").strip()
    if not origin or not os.path.isfile(origin):
        return rec
    path = os.path.normpath(str(rec.get("path") or ""))
    target = path if path and os.path.isfile(path) else copy_file_to_kb(origin)
    if not target:
        return rec
    try:
        shutil.copyfile(origin, target)
    except Exception:
        log.exception("同步知识库文件失败 origin=%s", origin)
        return rec
    rec["path"] = os.path.normpath(target)
    record_source_stamp(rec, origin)
    rec["updated_at"] = time.time()
    return rec


# OpenAI 兼容 API 的默认服务地址（用户留空时回退到它；页面上作为出厂占位值）
_API_BASE_DEFAULT = "https://api.deepseek.com/v1"


def default_api_base() -> str:
    return _API_BASE_DEFAULT


def normalize_api_base(raw: str) -> str:
    """把用户填的地址规整成 OpenAI 兼容 base_url。

    自动补 https://、去尾斜杠 / 尾巴上的 /chat/completions；
    只有主机没路径时补 /v1（多数兼容服务都挂在 /v1 下）。
    """
    h = (raw or "").strip()
    if not h:
        return _API_BASE_DEFAULT
    if not re.match(r"^https?://", h, re.I):
        h = "https://" + h
    h = re.sub(r"/chat/completions$", "", h, flags=re.I).rstrip("/")
    try:
        p = urllib.parse.urlparse(h)
        if not p.path.strip("/"):
            h = urllib.parse.urlunparse(
                (p.scheme or "https", p.netloc, "/v1", "", "", "")
            )
    except Exception:
        pass
    return h.rstrip("/")


def _read_text_file(path: str) -> str:
    raw = b""
    try:
        with open(path, "rb") as f:
            raw = f.read(_MAX_FILE_CHARS + 1)
    except Exception:
        log.debug("读取知识库文件失败 path=%s", path, exc_info=True)
        return ""
    if len(raw) > _MAX_FILE_CHARS:
        raw = raw[:_MAX_FILE_CHARS]
    text = ""
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            continue
    if not text:
        text = raw.decode("utf-8", errors="ignore")
    ext = os.path.splitext(path)[1].lower()
    if ext in (".html", ".htm"):
        text = _html_to_text(text)
    elif ext == ".json":
        text = _json_to_text(text)
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", s)
    s = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"[ \t]+\n", "\n", re.sub(r"[ \t]{2,}", " ", s))


def _json_to_text(s: str) -> str:
    try:
        data = json.loads(s)
    except Exception:
        return s
    return json.dumps(data, ensure_ascii=False, indent=2)


def chunk_text(text: str, max_len: int = _CHUNK_CHARS) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    paras = re.split(r"\n\s*\n", text)
    chunks: List[str] = []
    buf = ""
    for para in paras:
        p = para.strip()
        if not p:
            continue
        if len(buf) + len(p) + 1 <= max_len:
            buf = (buf + "\n" + p).strip() if buf else p
            continue
        if buf:
            chunks.append(buf)
        if len(p) <= max_len:
            buf = p
            continue
        for i in range(0, len(p), max_len):
            piece = p[i:i + max_len].strip()
            if piece:
                chunks.append(piece)
        buf = ""
    if buf:
        chunks.append(buf)
    return chunks


def _iter_rec_files(src: Dict[str, Any], seen: set) -> Iterable[str]:
    """展开单个来源记录指向的文本文件；重复文件只产出一次（跨记录全局去重）。"""
    kind = (src.get("kind") or "file").strip().lower()
    path = os.path.normpath(str(src.get("path") or "").strip())
    if not path:
        return
    if kind == "folder":
        if not os.path.isdir(path):
            return
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIR_NAMES and not d.startswith(".")]
            for name in files:
                ext = os.path.splitext(name)[1].lower()
                if ext not in _TEXT_EXTS:
                    continue
                fp = os.path.normpath(os.path.join(root, name))
                key = os.path.normcase(fp)
                if key in seen:
                    continue
                seen.add(key)
                yield fp
        return
    if os.path.isfile(path):
        key = os.path.normcase(path)
        if key in seen:
            return
        seen.add(key)
        yield path


def _iter_source_files(sources: Sequence[Dict[str, Any]]) -> Iterable[str]:
    seen = set()
    for src in sources or []:
        for fp in _iter_rec_files(src, seen):
            yield fp


def list_folder_files(path: str) -> List[str]:
    """返回文件夹中会被索引的文本文件路径（与 build_index 扫描规则一致）。"""
    path = os.path.normpath(path or "")
    if not path or not os.path.isdir(path):
        return []
    return list(_iter_source_files([{"kind": "folder", "path": path}]))


def is_supported_text_file(path: str) -> bool:
    """单个文件是否属于知识库可收录的文本类型（口径与 list_folder_files 一致）。"""
    path = os.path.normpath(path or "")
    return os.path.isfile(path) and os.path.splitext(path)[1].lower() in _TEXT_EXTS


def source_group(rec: Dict[str, Any]) -> str:
    """来源所属分类 id；未标注一律视为「未分类」。"""
    if isinstance(rec, dict):
        g = str(rec.get("group") or "").strip()
        if g:
            return g
    return GID_UNCAT


def build_index(sources: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """建索引：每段 chunk 记录其来源在本列表中的序号 src，
    供后续按分类过滤（未分类等分组不用重扫文件）。"""
    chunks: List[Dict[str, Any]] = []
    files = 0
    seen = set()
    for idx, src in enumerate(sources or []):
        for fp in _iter_rec_files(src, seen):
            text = _read_text_file(fp)
            if not text:
                continue
            files += 1
            title = os.path.basename(fp)
            for part in chunk_text(text):
                chunks.append({"source": fp, "title": title, "text": part, "src": idx})
                if len(chunks) >= _MAX_CHUNKS:
                    break
            if len(chunks) >= _MAX_CHUNKS:
                break
        if len(chunks) >= _MAX_CHUNKS:
            break
    return {
        "sources": [dict(s) for s in (sources or [])],
        "files": files,
        "chunks": chunks,
    }


def index_subset_for_groups(
    index: Dict[str, Any], active_gids: Sequence[str]
) -> Dict[str, Any]:
    """按「参与回答的分类」过滤整份索引：只保留属于勾选分类的来源片段。

    index 的 chunks 里 src 是来源在 sources 列表中的序号；根据该来源的
    group 是否在 active_gids 决定是否保留。未分类（含无 group 的老数据）
    不在 active_gids 中时自然被排除。
    """
    gids = set()
    for g in (active_gids or ()):
        g = str(g or "").strip()
        if g:
            gids.add(g)
    srcs = list((index or {}).get("sources") or [])
    keep_idx = set()
    for i, s in enumerate(srcs):
        if source_group(s) in gids:
            keep_idx.add(i)
    kept = []
    seen_files = set()
    for c in (index or {}).get("chunks") or []:
        try:
            si = int(c.get("src"))
        except (TypeError, ValueError):
            continue
        if si not in keep_idx:
            continue
        kept.append(c)
        try:
            seen_files.add(os.path.normcase(c.get("source") or ""))
        except Exception:
            pass
    return {
        "sources": [dict(s) for i, s in enumerate(srcs) if i in keep_idx],
        "files": len(seen_files),
        "chunks": kept,
    }


def save_index(index: Dict[str, Any]) -> None:
    path = index_file_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)
    os.replace(tmp, path)


def load_index() -> Dict[str, Any]:
    path = index_file_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("chunks"), list):
            return data
    except Exception:
        pass
    return {"sources": [], "files": 0, "chunks": []}


def _tokenize(text: str) -> List[str]:
    s = (text or "").lower()
    out: List[str] = []
    buf_cjk = []
    for m in _TOKEN_RE.finditer(s):
        tok = m.group(0)
        if _CJK_RE.fullmatch(tok):
            buf_cjk.append(tok)
            out.append(tok)
        else:
            if len(buf_cjk) >= 2:
                joined = "".join(buf_cjk)
                for i in range(len(joined) - 1):
                    out.append(joined[i:i + 2])
            buf_cjk = []
            if len(tok) >= 2:
                out.append(tok)
    if len(buf_cjk) >= 2:
        joined = "".join(buf_cjk)
        for i in range(len(joined) - 1):
            out.append(joined[i:i + 2])
    return out


def retrieve(index: Dict[str, Any], query: str, limit: int = 5) -> List[Dict[str, str]]:
    chunks = list((index or {}).get("chunks") or [])
    if not chunks:
        return []
    qtoks = _tokenize(query)
    if not qtoks:
        # 没有查询词时取各文件开头一段，避免空检索
        picked = []
        seen = set()
        for ch in chunks:
            src = os.path.normcase(ch.get("source") or "")
            if src in seen:
                continue
            seen.add(src)
            picked.append(ch)
            if len(picked) >= limit:
                break
        return picked

    qset = set(qtoks)
    scored: List[Tuple[float, int, Dict[str, str]]] = []
    for i, ch in enumerate(chunks):
        toks = _tokenize(ch.get("text") or "")
        if not toks:
            continue
        hit = 0
        for t in toks:
            if t in qset:
                hit += 1
        if hit <= 0:
            title = _tokenize(ch.get("title") or "")
            hit = sum(1 for t in title if t in qset) * 3
        if hit <= 0:
            continue
        score = hit / (1.0 + (len(toks) ** 0.5) * 0.15)
        scored.append((score, i, ch))
    scored.sort(key=lambda x: (-x[0], x[1]))

    # 按来源分组，每组内保持得分从高到低
    by_src: Dict[str, List[Dict[str, str]]] = {}
    for _score, _i, ch in scored:
        src = os.path.normcase(ch.get("source") or "")
        by_src.setdefault(src, []).append(ch)

    # 轮转取段：每个命中的文件先各取 1 段，再各取第 2 段……直到达到上限。
    # 避免某个长文件 / 末文件靠数量占满前几个槽位，保证覆盖所有相关文件。
    picked: List[Dict[str, str]] = []
    src_keys = list(by_src.keys())
    ptr = {s: 0 for s in src_keys}
    while len(picked) < limit:
        added = 0
        for s in src_keys:
            if len(picked) >= limit:
                break
            j = ptr[s]
            if j < len(by_src[s]):
                picked.append(by_src[s][j])
                ptr[s] = j + 1
                added += 1
        if not added:
            break
    return picked[:limit]


def kb_char_count(index: Dict[str, Any]) -> int:
    n = 0
    for ch in (index or {}).get("chunks") or []:
        n += len(ch.get("text") or "")
    return n


def format_passages(chunks: Sequence[Dict[str, str]]) -> str:
    parts = []
    for i, ch in enumerate(chunks, 1):
        title = ch.get("title") or os.path.basename(ch.get("source") or "")
        body = (ch.get("text") or "").strip()
        parts.append(f"[{i}] {title}\n{body}")
    return "\n\n".join(parts)


def _http_json(
    url: str,
    payload: Optional[dict] = None,
    timeout: float = 30,
    headers: Optional[dict] = None,
) -> Any:
    hdrs = {"Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    data = None
    method = "GET"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
        method = "POST"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:1200]
        except Exception:
            pass
        raise RuntimeError(_api_http_error(e.code, body)) from e
    except urllib.error.URLError as e:
        raise RuntimeError(_conn_error_text(url, e)) from e
    except TimeoutError as e:
        raise RuntimeError(f"连接 API 超时：{url}") from e
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except Exception as e:
        raise RuntimeError("API 返回的不是 JSON") from e


def _api_http_error(code: int, body: str) -> str:
    msg = (body or "").strip()
    try:
        data = json.loads(msg)
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                msg = str(err.get("message") or msg)
            else:
                msg = str(err or data.get("message") or msg)
    except Exception:
        pass
    msg = re.sub(r"\s+", " ", msg).strip()
    low = msg.lower()
    if code == 401 or "api key" in low or "authentication" in low:
        return f"API 密钥无效或无权限（HTTP {code}）：{msg or '请检查密钥'}"
    if code == 404 or "not exist" in low or "not found" in low or "model not" in low:
        return f"模型名或服务地址不对（HTTP {code}）：{msg or '请检查模型名与地址'}"
    if code == 429:
        return f"请求过快或额度不足（HTTP 429）：{msg or '请稍后再试'}"
    if code:
        return f"API 出错（HTTP {code}）：{msg or '未知错误'}"
    return msg or "API 出错"


def _conn_error_text(url: str, err: BaseException) -> str:
    reason = getattr(err, "reason", err)
    s = str(reason or "")
    low = s.lower()
    if (
        "10061" in s
        or "积极拒绝" in s
        or "actively refused" in low
        or "connection refused" in low
    ):
        return f"连不上 API 服务（{url}）：连接被拒绝，请检查服务地址与端口。"
    if "timed out" in low or "timeout" in low or "超时" in s:
        return f"连接 API 超时：{url}"
    if "certificate" in low or "ssl" in low:
        return f"连接 API 失败（{url}）：SSL 证书校验失败"
    return f"连不上 API（{url}）：{reason}"


def _bearer_headers(api_key: str) -> dict:
    key = (api_key or "").strip()
    if key:
        return {"Authorization": f"Bearer {key}"}
    return {}


def chat(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    *,
    max_tokens: Optional[int] = None,
    timeout: float = 180,
) -> str:
    """OpenAI 兼容 /chat/completions 调用，返回助手正文；失败抛 RuntimeError。"""
    model = (model or "").strip()
    if not model:
        raise RuntimeError("还没有填模型名（在「大模型」卡片里填写）")
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "temperature": 0.2,
    }
    if max_tokens is not None:
        payload["max_tokens"] = int(max_tokens)
    url = normalize_api_base(base_url) + "/chat/completions"
    data = _http_json(
        url, payload, timeout=timeout, headers=_bearer_headers(api_key)
    )
    choices = (data or {}).get("choices") or []
    for ch in choices:
        if not isinstance(ch, dict):
            continue
        message = ch.get("message") or {}
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    raise RuntimeError("API 没有返回内容")


def list_api_models(base_url: str, api_key: str, *, timeout: float = 30) -> List[str]:
    """从服务商拉取可用模型列表：GET {base}/models。

    返回模型 id 排序去重后的列表。拉取本身即验证了地址 / 密钥连通；
    失败抛 RuntimeError。个别服务不支持 /models，会返回 404。
    """
    url = normalize_api_base(base_url) + "/models"
    data = _http_json(url, timeout=timeout, headers=_bearer_headers(api_key))
    ids: List[str] = []
    arr = (data or {}).get("data")
    if not isinstance(arr, list):
        return ids
    for m in arr:
        if not isinstance(m, dict):
            continue
        mid = str((m.get("id") or m.get("model") or "") or "").strip()
        if mid:
            ids.append(mid)
    return sorted(set(ids))


def _norm_txt(s: str) -> str:
    """答案去重 / 回显判断用：去掉空白与标点后归一。"""
    return re.sub(r"[\s\W_]+", "", (s or "")).lower()


def _is_title_echo(answer: str, source: str, title: str) -> bool:
    """判断 answer 是否只是把文件名 / 标题回显了一遍（模型偷懒的典型产物）。"""
    a = _norm_txt(answer)
    if not a or len(a) >= 40:
        return False
    stem = _norm_txt(os.path.splitext(os.path.basename(source or ""))[0])
    return bool(stem and a == stem) or bool(_norm_txt(title) == a)


def parse_answers(raw: str) -> List[Dict[str, str]]:
    s = (raw or "").strip()
    if not s:
        return []
    fence = re.search(r"```(?:json)?\s*([\s\S]+?)```", s, re.I)
    if fence:
        s = fence.group(1).strip()
    blob = s
    if not (blob.startswith("[") or blob.startswith("{")):
        m = re.search(r"(\[[\s\S]+\]|\{[\s\S]+\})", blob)
        if m:
            blob = m.group(1)
    data = None
    try:
        data = json.loads(blob)
    except Exception:
        data = None
    items: List[Any] = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for key in ("answers", "results", "items", "options"):
            v = data.get(key)
            if isinstance(v, list):
                items = v
                break
        if not items:
            items = [data]
    out: List[Dict[str, str]] = []
    for it in items:
        if isinstance(it, str) and it.strip():
            out.append({"title": f"答案 {len(out) + 1}", "source": "", "answer": it.strip()})
            continue
        if not isinstance(it, dict):
            continue
        answer = (
            it.get("answer") or it.get("content") or it.get("text")
            or it.get("body") or ""
        )
        if not isinstance(answer, str):
            answer = str(answer or "")
        answer = answer.strip()
        if not answer:
            continue
        title = it.get("title") or it.get("name") or it.get("label") or ""
        if not isinstance(title, str) or not title.strip():
            title = f"答案 {len(out) + 1}"
        source = it.get("source") or it.get("file") or ""
        if not isinstance(source, str):
            source = str(source or "")
        source = re.sub(r"^\[\d+\]\s*", "", source).strip()
        source = source.strip("`").strip()
        out.append({
            "title": title.strip()[:80],
            "source": source.strip(),
            "answer": answer,
        })
    if not out:
        # 模型没按 JSON 来：整段当一条
        return [{"title": "答案 1", "source": "", "answer": (raw or "").strip()}]

    # 去重：内容归一后相同的只保留第一条（模型常凑数重复）
    seen: set = set()
    uniq: List[Dict[str, str]] = []
    for it in out:
        key = _norm_txt(it.get("answer") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        uniq.append(it)

    # 回显型答案（把文件名/标题当正文）降级：有真答案就去掉，只剩它则保留第一条
    real = [it for it in uniq if not _is_title_echo(it.get("answer") or "", it.get("source") or "", it.get("title") or "")]
    if real:
        return real[:3]
    return uniq[:1]


def build_answer_prompt(query: str, passages: str, *, has_image: bool) -> str:
    q = (query or "").strip() or "（截图中未能提取到文字）"
    kb = (passages or "").strip() or "（知识库为空或没有匹配片段）"
    img_hint = (
        "玩家提供了一张游戏截图，请结合画面内容和知识库作答。"
        if has_image else
        "请根据截图识别出的文字和知识库作答。"
    )
    return (
        "你是游戏攻略助手。" + img_hint + "\n"
        "画面里识别到的文字或情境：\n"
        f"{q}\n\n"
        "攻略知识库相关片段（[N] 后面是来源文件名和正文）：\n"
        f"{kb}\n\n"
        "直接回答用户的问题，答案写成给玩家看的、通顺的普通正文，"
        "要有具体内容（原因、做法、注意事项），不要只重复文件名或标题，不要罗列片段编号。"
        "每条答案必须从上面某一片段找依据；source 字段填该片段 [N] 后的来源文件名，不得编造。"
        "只有确有多种不同的解答时才输出多条；内容相同就只输出 1 条，禁止凑数重复。\n"
        "只输出 JSON 数组，不要 markdown 围栏，不要解说：\n"
        '[{"title":"短标题","source":"来源文件名","answer":"完整解答正文"}]\n'
        "最多 3 条。片段不足时，title 用「不确定」，answer 说明缺什么。"
    )


def build_extract_prompt() -> str:
    return (
        "这是一张游戏截图。请提取画面里的任务、选项、对话、物品名、关卡提示等关键文字，"
        "再用一两句话概括玩家当前面对的问题。只输出纯文本，不要 JSON。"
    )
