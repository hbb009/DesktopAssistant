# utils/gamebrain.py
# 游戏大脑：攻略知识库索引 / 检索 / OpenAI 兼容大模型 API 对话。不 import Qt。

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


def gamebrain_dir() -> str:
    try:
        from utils.app_paths import gamebrain_dir as _d
        return _d()
    except Exception:
        d = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "gamebrain",
        )
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        return d


def index_file_path() -> str:
    return os.path.join(gamebrain_dir(), "index.json")


# gamebrain/ 根下由程序自己占用的文件，不能当知识库副本覆盖或删掉
_KB_RESERVED_NAMES = frozenset({
    "index.json",
    "index.json.tmp",
    "last_shot.png",
})


def kb_files_dir() -> str:
    """知识库托管目录：用户加入的文件直接放在 gamebrain/ 下（不再用 kb 子目录）。"""
    return gamebrain_dir()


def _legacy_kb_dirs() -> list:
    """仅本页自己的旧 kb 子目录。不要指向游戏助手的 gameassist/。"""
    dest = gamebrain_dir()
    return [os.path.join(dest, "kb")]


def _legacy_managed_roots() -> list:
    """仅本页托管根。不要扫游戏助手目录。"""
    return _legacy_kb_dirs()


def _norm_abs(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _is_reserved_kb_name(name: str) -> bool:
    return os.path.normcase(os.path.basename(name or "")) in {
        os.path.normcase(n) for n in _KB_RESERVED_NAMES
    }


def _is_under_dir(path: str, root: str) -> bool:
    try:
        ap = _norm_abs(path)
        rp = _norm_abs(root)
        if ap == rp:
            return False
        return ap.startswith(rp + os.sep)
    except Exception:
        return False


def _is_under_kb(path: str) -> bool:
    """是否为知识库托管副本（gamebrain/ 下，不含索引/截图等运行时文件）。

    旧版 game_assist/、game_assist/kb/ 里的文件也算托管副本，方便迁移后删掉残留。
    """
    path = path or ""
    if not path:
        return False
    for old in _legacy_managed_roots():
        if _is_under_dir(path, old):
            return True
    root = gamebrain_dir()
    if not _is_under_dir(path, root):
        return False
    try:
        if _norm_abs(os.path.dirname(path)) == _norm_abs(root) and _is_reserved_kb_name(path):
            return False
    except Exception:
        return False
    return True


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

    def _cand(i=None) -> str:
        if i is None:
            return os.path.join(d, base + ext)
        return os.path.join(d, f"{base}_{i}{ext}")

    target = _cand()
    if not os.path.exists(target) and not _is_reserved_kb_name(target):
        return os.path.normpath(target)
    for i in range(1, 1000):
        cand = _cand(i)
        if not os.path.exists(cand) and not _is_reserved_kb_name(cand):
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
    if _is_reserved_kb_name(name):
        return _unique_kb_target(name)
    return os.path.normpath(os.path.join(kb_files_dir(), name))


def overwrite_kb_copy(src: str, target: str) -> bool:
    """用源文件覆盖知识库托管目录内的目标副本；失败返回 False。"""
    src = os.path.normpath(src or "")
    target = _rewrite_legacy_kb_path(os.path.normpath(target or ""))
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


def _rewrite_legacy_kb_path(path: str) -> str:
    """旧路径（game_assist/、…/kb/）改写到 gameassist/ 根下；对不上则原样返回。"""
    path = os.path.normpath(path or "")
    if not path:
        return path
    dest = gamebrain_dir()
    try:
        ap = os.path.abspath(path)
    except Exception:
        return path
    if _norm_abs(os.path.dirname(ap)) == _norm_abs(dest):
        return os.path.normpath(ap)
    for old in _legacy_managed_roots():
        try:
            old_abs = os.path.abspath(old)
        except Exception:
            continue
        prefix = os.path.normcase(old_abs) + os.sep
        if os.path.normcase(ap).startswith(prefix) or _norm_abs(ap) == os.path.normcase(old_abs):
            return os.path.normpath(os.path.join(dest, os.path.basename(ap)))
    return os.path.normpath(path)


def flatten_legacy_kb_dir(sources: Sequence[Dict[str, Any]] = ()) -> int:
    """把旧版 …/kb/ 里的文件挪到 gameassist/ 根下，并改写来源 path。"""
    root = gamebrain_dir()
    n = 0
    mapping: Dict[str, str] = {}
    for old in _legacy_kb_dirs():
        if not os.path.isdir(old):
            continue
        try:
            names = os.listdir(old)
        except Exception:
            names = []
        for name in names:
            src = os.path.join(old, name)
            if not os.path.isfile(src):
                continue
            dest = os.path.join(root, name)
            if _is_reserved_kb_name(name):
                dest = _unique_kb_target(name)
            src_key = _norm_abs(src)
            try:
                if os.path.normcase(os.path.abspath(src)) == os.path.normcase(
                    os.path.abspath(dest)
                ):
                    continue
            except Exception:
                pass
            if os.path.exists(dest):
                mapping[src_key] = os.path.normpath(dest)
                try:
                    os.remove(src)
                    n += 1
                except Exception:
                    log.exception("删除旧 kb 重复文件失败 path=%s", src)
                continue
            try:
                os.rename(src, dest)
            except Exception:
                try:
                    shutil.move(src, dest)
                except Exception:
                    log.exception("迁移知识库文件失败 src=%s dest=%s", src, dest)
                    continue
            mapping[src_key] = os.path.normpath(dest)
            n += 1
        try:
            if not os.listdir(old):
                os.rmdir(old)
        except Exception:
            pass

    for s in sources or []:
        if not isinstance(s, dict) or (s.get("kind") or "file") != "file":
            continue
        path = str(s.get("path") or "").strip()
        if not path:
            continue
        key = _norm_abs(path)
        if key in mapping:
            s["path"] = mapping[key]
            n += 1
            continue
        rewritten = _rewrite_legacy_kb_path(path)
        if rewritten != os.path.normpath(path) and os.path.isfile(rewritten):
            s["path"] = rewritten
            n += 1
    return n


def relocate_missing_file_sources(sources: Sequence[Dict[str, Any]]) -> int:
    """把指向旧托管目录（data/game_assist、game_assist、…/kb）已失效路径的文件来源，
    归位到当前托管目录 gamebrain/ 下，缺文件时从源文件复制。

    返回改动的数量。解决 v9.17 目录迁移后，user.txt 里残留旧路径
    导致“卡片在、但永远读不到内容”的问题。
    """
    n = flatten_legacy_kb_dir(sources)
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

    只在路径确实位于知识库托管目录内时才删除，防止误删用户的原始文件。
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


# 游戏大脑默认走 OpenRouter 上的 Jev Decisions API（见 docs/jev_api.md）
_API_BASE_DEFAULT = "https://openrouter.ai/api/v1"
_JEV_MODEL = "typesafe/jev-1.13"
_JEV_MODEL_LEGACY = "typesafe/jev"
_JEV_MODEL_LATEST = "~typesafe/jev-latest"
# 不再内置任何密钥：在「游戏大脑」页填写，或设置环境变量 OPENROUTER_API_KEY
_DEFAULT_API_KEY = ""
# 可选来源头（docs/jev_api.md 步骤 1）；值可自定义，便于 OpenRouter 后台区分来源
_OPENROUTER_REFERER = "http://localhost:3000"
_OPENROUTER_TITLE = "My Desktop Game Assistant"

# 画面里抽不到明确选项时，给 Decisions Choice 的兜底动作（ASCII key → 中文说明）
_JEV_FALLBACK_CRITERIA = {
    "continue": "继续推进当前目标或剧情",
    "wait": "等待观望，暂不行动",
    "attack": "进攻或主动出击",
    "defend": "防御、躲避或保命",
    "heal": "回血、补给或恢复状态",
    "flee": "撤退或逃离当前局面",
}


def default_api_base() -> str:
    return _API_BASE_DEFAULT


def default_jev_model() -> str:
    return _JEV_MODEL


def default_api_key() -> str:
    """页面里没填 Key 时的兜底：环境变量 OPENROUTER_API_KEY（可为空）。"""
    return (os.environ.get("OPENROUTER_API_KEY", "") or _DEFAULT_API_KEY).strip()


def is_openrouter_base(base_url: str) -> bool:
    h = (base_url or "").lower()
    return "openrouter.ai" in h


def is_jev_model(model: str) -> bool:
    """是否为 Jev 决策模型（含 legacy typesafe/jev 与 ~typesafe/jev* 别名）。"""
    m = (model or "").strip().lower()
    if not m:
        return False
    if m.startswith("~"):
        body = m[1:]
    else:
        body = m
    return body == "typesafe/jev" or body.startswith("typesafe/jev")


def normalize_jev_model(model: str) -> str:
    """请求用模型 id：legacy typesafe/jev → typesafe/jev-1.13；其余 jev* 原样保留。"""
    m = (model or "").strip()
    if not m:
        return _JEV_MODEL
    low = m.lower()
    if low == _JEV_MODEL_LEGACY or low == "typesafe/jev":
        return _JEV_MODEL
    if is_jev_model(m):
        return m
    return _JEV_MODEL


def jev_decisions_url(base_url: str) -> str:
    """OpenRouter Decisions 端点。从 openrouter base 推导宿主，不挂在 /v1/chat/completions 下。"""
    if is_openrouter_base(base_url):
        try:
            p = urllib.parse.urlparse(normalize_api_base(base_url))
            host = p.netloc or "openrouter.ai"
            scheme = p.scheme or "https"
            return f"{scheme}://{host}/api/alpha/decisions"
        except Exception:
            pass
    return "https://openrouter.ai/api/alpha/decisions"


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


def _openrouter_headers(api_key: str) -> dict:
    """OpenRouter 鉴权 + 可选来源头（docs/jev_api.md 步骤 1）。"""
    hdrs = _bearer_headers(api_key)
    hdrs["HTTP-Referer"] = _OPENROUTER_REFERER
    hdrs["X-Title"] = _OPENROUTER_TITLE
    return hdrs


def extract_action_enum(text: str, limit: int = 8) -> List[str]:
    """从识别文字里抽选项（A/B/C… 或 1/2/3…）。不足 2 条返回空列表。"""
    blob = text or ""
    patterns = (
        re.compile(r"(?m)^\s*(?:选项\s*)?([A-D])[.．、:：\)）]\s*(.+?)\s*$"),
        re.compile(r"(?m)^\s*([1-8])[.．、:：\)）]\s*(.+?)\s*$"),
    )
    for pat in patterns:
        found: List[str] = []
        seen = set()
        for m in pat.finditer(blob):
            item = f"{m.group(1)}. {m.group(2).strip()}"
            item = item[:48]
            key = item.casefold()
            if not item or key in seen:
                continue
            seen.add(key)
            found.append(item)
            if len(found) >= limit:
                break
        if len(found) >= 2:
            return found
    return []


def jev_response_format(actions: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """兼容旧调用：曾用于 chat/completions + json_schema；Jev 现走 Decisions API。"""
    action_prop: Dict[str, Any] = {
        "type": "string",
        "description": "玩家此刻应采取的动作或选择",
    }
    opts = [str(x).strip() for x in (actions or []) if str(x).strip()]
    if len(opts) >= 2:
        action_prop["enum"] = opts[:8]
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "game_decision",
            "schema": {
                "type": "object",
                "properties": {
                    "action": action_prop,
                    "reason": {
                        "type": "string",
                        "description": "做出该决策的简要理由",
                    },
                },
                "required": ["action"],
            },
        },
    }


def _jev_build_action_question(query: str) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """构造 Decisions Choice 问题；返回 (questions, key→展示文案)。

    有 >=2 个抽到的选项时用 opt_1/opt_2… 作 ASCII 安全 key，criteria 值为中文标签；
    否则用通用游戏动作兜底。
    """
    opts = extract_action_enum(query)
    key_to_label: Dict[str, str] = {}
    if len(opts) >= 2:
        criteria: Dict[str, str] = {}
        for i, opt in enumerate(opts[:8], 1):
            key = f"opt_{i}"
            label = str(opt).strip()
            criteria[key] = label
            key_to_label[key] = label
    else:
        criteria = dict(_JEV_FALLBACK_CRITERIA)
        key_to_label = dict(_JEV_FALLBACK_CRITERIA)
    questions = {
        "action": {
            "type": "choice",
            "instructions": "玩家此刻应采取的动作或选择",
            "criteria": criteria,
        }
    }
    return questions, key_to_label


def _jev_format_decision_content(
    choice_key: str,
    key_to_label: Dict[str, str],
    *,
    confidence: Any = None,
    probabilities: Any = None,
) -> str:
    """把 Decisions answers.action 收成 parse_answers 可吃的 JSON 字符串。"""
    label = key_to_label.get(choice_key) or choice_key or "（无选择）"
    reason_parts: List[str] = []
    if confidence is not None:
        try:
            reason_parts.append(f"confidence={float(confidence):.2f}")
        except (TypeError, ValueError):
            reason_parts.append(f"confidence={confidence}")
    if isinstance(probabilities, dict) and probabilities:
        ranked = sorted(
            probabilities.items(),
            key=lambda kv: -float(kv[1] or 0) if _is_number(kv[1]) else 0.0,
        )
        bits: List[str] = []
        for k, p in ranked[:6]:
            lab = key_to_label.get(str(k), str(k))
            try:
                bits.append(f"{lab}={float(p):.0%}")
            except (TypeError, ValueError):
                bits.append(f"{lab}={p}")
        if bits:
            reason_parts.append("probs: " + ", ".join(bits))
    reason = "; ".join(reason_parts)
    return json.dumps({"action": label, "reason": reason}, ensure_ascii=False)


def _is_number(v: Any) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def jev_decisions(
    base_url: str,
    api_key: str,
    model: str,
    state_text: str,
    *,
    timeout: float = 180,
    extra_headers: Optional[dict] = None,
) -> str:
    """调用 OpenRouter Decisions API（Jev）。返回可交给 parse_answers 的 JSON 文本。"""
    model_id = normalize_jev_model(model)
    raw_state = (state_text or "").strip()
    screen = build_jev_user_content(raw_state)
    questions, key_to_label = _jev_build_action_question(raw_state or screen)
    payload: Dict[str, Any] = {
        "model": model_id,
        "state": {"screen": screen},
        "questions": questions,
    }
    url = jev_decisions_url(base_url)
    if extra_headers:
        headers = dict(extra_headers)
    elif is_openrouter_base(base_url) or "openrouter.ai" in url.lower():
        headers = _openrouter_headers(api_key)
    else:
        headers = _bearer_headers(api_key)
    data = _http_json(url, payload, timeout=timeout, headers=headers)
    answers = (data or {}).get("answers") or {}
    action = answers.get("action") if isinstance(answers, dict) else None
    if not isinstance(action, dict):
        raise RuntimeError("Jev Decisions 没有返回 answers.action")
    choice = action.get("choice")
    if not isinstance(choice, str) or not choice.strip():
        raise RuntimeError("Jev Decisions 没有返回 action.choice")
    return _jev_format_decision_content(
        choice.strip(),
        key_to_label,
        confidence=action.get("confidence"),
        probabilities=action.get("probabilities"),
    )


def chat(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    *,
    max_tokens: Optional[int] = None,
    timeout: float = 180,
    response_format: Optional[dict] = None,
    extra_headers: Optional[dict] = None,
) -> str:
    """对话入口：Jev 走 Decisions API；其它模型走 OpenAI 兼容 /chat/completions。"""
    model = (model or "").strip()
    if not model:
        raise RuntimeError("还没有填模型名（在「大模型 API」卡片里填写）")
    if is_jev_model(model):
        return jev_decisions(
            base_url, api_key, model, prompt,
            timeout=timeout, extra_headers=extra_headers,
        )
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "temperature": 0.2,
    }
    if response_format:
        payload["response_format"] = response_format
    if max_tokens is not None:
        payload["max_tokens"] = int(max_tokens)
    url = normalize_api_base(base_url) + "/chat/completions"
    if extra_headers:
        headers = dict(extra_headers)
    elif is_openrouter_base(base_url):
        headers = _openrouter_headers(api_key)
    else:
        headers = _bearer_headers(api_key)
    data = _http_json(url, payload, timeout=timeout, headers=headers)
    choices = (data or {}).get("choices") or []
    for ch in choices:
        if not isinstance(ch, dict):
            continue
        message = ch.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, str) and part.strip():
                    parts.append(part.strip())
                elif isinstance(part, dict):
                    t = part.get("text") or part.get("content") or ""
                    if isinstance(t, str) and t.strip():
                        parts.append(t.strip())
            content = "\n".join(parts)
        if isinstance(content, dict):
            try:
                content = json.dumps(content, ensure_ascii=False)
            except Exception:
                content = str(content)
        if isinstance(content, str) and content.strip():
            return content.strip()
    raise RuntimeError("API 没有返回内容")


def list_api_models(base_url: str, api_key: str, *, timeout: float = 30) -> List[str]:
    """从服务商拉取可用模型列表：GET {base}/models。

    返回模型 id 排序去重后的列表。拉取本身即验证了地址 / 密钥连通；
    失败抛 RuntimeError。个别服务不支持 /models，会返回 404。
    """
    url = normalize_api_base(base_url) + "/models"
    headers = (
        _openrouter_headers(api_key)
        if is_openrouter_base(base_url)
        else _bearer_headers(api_key)
    )
    data = _http_json(url, timeout=timeout, headers=headers)
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
    ids = list(dict.fromkeys(ids))  # 保序去重
    # OpenRouter 公开 /models 常不包含 Jev；钉 typesafe/jev-1.13 置顶，去掉 legacy id
    if is_openrouter_base(base_url):
        ids = [x for x in ids if not is_jev_model(x)]
        ids.insert(0, _JEV_MODEL)
        return ids
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


def _dedupe_consecutive_lines(text: str) -> str:
    """去掉相邻重复行（含仅空白差异），避免 action/reason 或 title+body 叠字。"""
    lines = (text or "").splitlines()
    out: List[str] = []
    prev_norm = None
    for line in lines:
        raw = line.rstrip()
        # 保留空行分隔，但不连续堆空行
        if not raw.strip():
            if out and out[-1] != "":
                out.append("")
            prev_norm = ""
            continue
        norm = _norm_txt(raw)
        if norm and norm == prev_norm:
            continue
        out.append(raw)
        prev_norm = norm
    # 去掉首尾空行
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)


def _format_action_reason(action: str, reason: str = "") -> str:
    """把 action / reason 收成单条展示正文：不重复 action，reason 相同时省略。"""
    action_s = (action or "").strip()
    reason_s = (reason or "").strip()
    if not action_s:
        return _dedupe_consecutive_lines(reason_s)
    if not reason_s:
        return action_s
    if _norm_txt(reason_s) == _norm_txt(action_s):
        return action_s
    # reason 若以 action 开头再跟说明，剥掉重复首行
    r_lines = [ln.rstrip() for ln in reason_s.splitlines()]
    if r_lines and _norm_txt(r_lines[0]) == _norm_txt(action_s):
        rest = "\n".join(r_lines[1:]).strip()
        if rest:
            return _dedupe_consecutive_lines(f"{action_s}\n{rest}")
        return action_s
    return _dedupe_consecutive_lines(f"{action_s}\n{reason_s}")


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
        action = data.get("action")
        if isinstance(action, str) and action.strip():
            reason = data.get("reason")
            reason_s = reason.strip() if isinstance(reason, str) else ""
            body = _format_action_reason(action, reason_s)
            return [{
                "title": action.strip()[:80],
                "source": "",
                "answer": body,
            }]
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
            out.append({"title": f"答案 {len(out) + 1}", "source": "", "answer": _dedupe_consecutive_lines(it.strip())})
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
            "answer": _dedupe_consecutive_lines(answer),
        })
    if not out:
        # 模型没按 JSON 来：整段当一条
        return [{"title": "答案 1", "source": "", "answer": _dedupe_consecutive_lines((raw or "").strip())}]

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


def build_jev_user_content(query: str) -> str:
    """Jev 用户消息（对齐 docs/jev_api.md 步骤 2）：仅传游戏状态，不夹知识库。"""
    q = (query or "").strip()
    if not q:
        q = "（无游戏状态）"
    prefix = "当前游戏画面识别结果："
    if q.startswith(prefix) or q.startswith("当前游戏画面识别结果"):
        return q
    return f"{prefix}{q}"


def build_answer_prompt(query: str, passages: str, *, has_image: bool) -> str:
    """兼容旧调用：有知识库片段时附上；否则等同 build_jev_user_content。"""
    del has_image
    q = (query or "").strip() or "（截图中未能提取到文字）"
    kb = (passages or "").strip()
    if not kb:
        return build_jev_user_content(q)
    return (
        f"当前游戏画面识别结果：{q}\n\n"
        "攻略知识库相关片段（[N] 后面是来源文件名和正文）：\n"
        f"{kb}\n\n"
        "请根据画面与攻略，给出玩家此刻应采取的动作。"
        "若识别结果里有明确选项，必须从这些选项里选一个。"
    )


def build_extract_prompt() -> str:
    return (
        "这是一张游戏截图。请提取画面里的任务、选项、对话、物品名、关卡提示等关键文字，"
        "再用一两句话概括玩家当前面对的问题。只输出纯文本，不要 JSON。"
    )
