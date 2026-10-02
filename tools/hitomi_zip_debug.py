# -*- coding: utf-8 -*-
"""
hitomi.la ZIP 下载 Debug 工具
============================

用途
----
1. 核实官网「Download」按钮到底怎么拿到 .zip
2. 探测是否存在「服务端整包 ZIP」接口
3. 按官网 download.js 同款逻辑：逐张拉 webp → 本地打成单个 .zip

结论（本工具会再次实测）
------------------------
官网页面加载了：
  - jszip.min.js / FileSaver.min.js / download.js
  - #dl-button 点击后走 download_gallery()

download.js 流程（客户端，非服务端整包）：
  1. 读 galleryinfo.files
  2. 用 url_from_url_from_hash(..., 'webp') 拼每张图 CDN 直链
  3. 约 1 秒间隔串行 XHR 拉 arraybuffer
  4. JSZip.file(...) 写入
  5. zip.generateAsync → saveAs(title + ".zip")

任何 /download/{id}.zip、/galleries/{id}.zip 之类服务端整包 URL 均为 404。
因此程序侧「单一 ZIP」只能复刻官网：拉图 + 本地 zip，而不是「一次 GET 拿整包」。

用法
----
  python tools/hitomi_zip_debug.py <图库链接或 ID>
  python tools/hitomi_zip_debug.py https://hitomi.la/galleries/2846995.html
  python tools/hitomi_zip_debug.py 2846995 --no-download   # 只探测，不真正下 zip
  python tools/hitomi_zip_debug.py 2846995 --sample 3        # 只下前 3 张做烟测
  python tools/hitomi_zip_debug.py 2846995 --out D:/tmp

输出
----
  data/hitomi_zip_debug_<id>_<stamp>.txt      文本报告
  <out>/<安全标题>.zip                        实测 zip（默认 data/）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

# 允许直接 python tools/xxx.py 时找到项目根
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    import requests
except ImportError:
    print("需要 requests：pip install requests")
    sys.exit(1)

# ── 与 page_gallery 对齐的常量（独立脚本，不 import 大页以免拉起 Qt）──────────
_HITOMI_LTN_HOSTS = (
    "ltn.gold-usergeneratedcontent.net",
    "ltn.hitomi.la",
)
_HITOMI_CDN_DOMAIN = "gold-usergeneratedcontent.net"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_REFERER = "https://hitomi.la/"

# 官网 download.js：throttle_interval_ms = 1000
_OFFICIAL_THROTTLE_MS = 1000


# ── 日志 ─────────────────────────────────────────────────────────────────────

class _Log:
    def __init__(self):
        self.lines: list[str] = []

    def __call__(self, msg: str, level: str = "info"):
        line = f"[{level:4}] {msg}"
        self.lines.append(line)
        print(line, flush=True)


# ── ID / 元数据 / gg.js（与主程序同算法）────────────────────────────────────

def extract_hitomi_id(text: str) -> int:
    text = (text or "").strip()
    if not text:
        raise ValueError("空文本")
    m = re.search(
        r"hitomi\.la/galleries/(\d{4,10})(?:\.html)?(?:[#?]|$)", text, re.I
    )
    if m:
        return int(m.group(1))
    m = re.search(
        r"hitomi\.la/(?:galleries|reader|doujinshi|manga|artistcg|gamecg|imageset|anime)/"
        r"[^\s\"'<>]*?-(\d{4,10})(?:\.html)?(?:[#?]|$)",
        text,
        re.I,
    )
    if m:
        return int(m.group(1))
    if re.fullmatch(r"\d{4,10}", text):
        return int(text)
    m2 = re.search(
        r"hitomi\.la/[^\s\"'<>]*?-(\d{4,10})(?:\.html)?(?:[#?\s]|$)", text, re.I
    )
    if m2:
        return int(m2.group(1))
    cands = re.findall(r"(\d{5,10})", text)
    if cands:
        return int(max(cands, key=len))
    raise ValueError("未找到 hitomi.la 图库 ID")


def _make_session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.headers.update({
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    return s


def _fetch_ltn(session: requests.Session, path: str, log: _Log) -> str:
    last_err = None
    for attempt in range(3):
        for host in _HITOMI_LTN_HOSTS:
            url = f"https://{host}{path}"
            try:
                r = session.get(url, timeout=30, headers={"Referer": _REFERER})
                if r.status_code == 404:
                    last_err = RuntimeError(f"404 {url}")
                    continue
                r.raise_for_status()
                return r.text
            except Exception as e:
                last_err = e
                log(f"  ltn 失败 {host}: {e}", "warn")
        if attempt < 2:
            time.sleep((attempt + 1) * 2)
    raise RuntimeError(f"无法访问 Hitomi 元数据: {last_err}")


def _load_gg(session: requests.Session, log: _Log) -> tuple[set, int, int, str]:
    text = _fetch_ltn(session, "/gg.js", log)
    cases = set(int(x) for x in re.findall(r"case\s+(\d+)\s*:", text))
    if not cases:
        raise RuntimeError("gg.js 解析失败：无 case")
    mo = re.search(r"var\s+o\s*=\s*(\d+)\s*;", text)
    default_m = int(mo.group(1)) if mo else 0
    case_assigns = re.findall(r"o\s*=\s*(\d+)\s*;\s*break", text)
    case_m = int(case_assigns[-1]) if case_assigns else (0 if default_m else 1)
    mb = re.search(r"b:\s*'([^']+)'", text) or re.search(r'b:\s*"([^"]+)"', text)
    b = (mb.group(1) if mb else "")
    if not b:
        raise RuntimeError("gg.js 解析失败：无 b")
    if not b.endswith("/"):
        b += "/"
    log(f"gg.js · cases={len(cases)} · m=({default_m}/{case_m}) · b={b}", "ok")
    return cases, default_m, case_m, b


def _gg_s(hash_: str) -> str:
    h = (hash_ or "").strip()
    m = re.search(r"(..)(.)$", h)
    if not m:
        raise ValueError(f"无效 hash: {hash_!r}")
    return str(int(m.group(2) + m.group(1), 16))


def _gg_m(code: int, cases: set, default_m: int, case_m: int) -> int:
    return int(case_m) if int(code) in cases else int(default_m)


def _webp_url(hash_: str, cases: set, default_m: int, case_m: int, b: str) -> str:
    """对齐 common.js url_from_url_from_hash(..., 'webp')。"""
    h = (hash_ or "").strip()
    code_s = _gg_s(h)
    code = int(code_s)
    sub = "w" + str(1 + _gg_m(code, cases, default_m, case_m))
    path = f"{b}{code_s}/{h}.webp"
    return f"https://{sub}.{_HITOMI_CDN_DOMAIN}/{path}"


def _zip_entry_name(orig_name: str, index: int) -> str:
    """官网：image.name 扩展名改成 webp。"""
    raw = (orig_name or "").strip() or f"{index:04d}.png"
    base = os.path.basename(raw.replace("\\", "/"))
    stem = base.rsplit(".", 1)[0] if "." in base else base
    stem = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", stem).strip(" .") or f"{index:04d}"
    return f"{stem}.webp"


def _safe_name(name: str, fallback: str = "hitomi", max_len: int = 80) -> str:
    name = (name or "").strip()
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name)
    name = re.sub(r"[ \t]+", " ", name)
    name = re.sub(r"_+", "_", name).strip(" .") or fallback
    if len(name) > max_len:
        name = name[:max_len].rstrip(" .") or fallback
    return name


def _is_webp(data: bytes) -> bool:
    return (
        isinstance(data, (bytes, bytearray))
        and len(data) >= 12
        and data[:4] == b"RIFF"
        and data[8:12] == b"WEBP"
    )


# ── 阶段 1：页面 + download.js 取证 ──────────────────────────────────────────

def phase_page_and_scripts(session: requests.Session, gid: int, log: _Log) -> dict:
    """拉作品页 + download.js，证明 Download = 客户端 JSZip。"""
    info = {
        "page_url": f"https://hitomi.la/galleries/{gid}.html",
        "page_status": 0,
        "has_dl_button": False,
        "scripts": [],
        "download_js_url": "",
        "download_js_ok": False,
        "download_js_is_client_zip": False,
        "download_js_snippets": [],
    }
    log("── 阶段 1：作品页 + download.js ──", "info")
    r = session.get(info["page_url"], timeout=30, headers={"Referer": _REFERER})
    info["page_status"] = r.status_code
    html = r.text or ""
    log(f"作品页 HTTP {r.status_code} · {len(html)} B", "ok" if r.ok else "err")

    info["has_dl_button"] = bool(
        re.search(r'id=["\']dl-button["\']', html) or re.search(r">\s*Download\s*<", html)
    )
    log(f"页面含 #dl-button / Download：{info['has_dl_button']}", "ok" if info["has_dl_button"] else "warn")

    scripts = re.findall(r'src=["\']([^"\']+)["\']', html)
    info["scripts"] = scripts
    for key in ("jszip", "FileSaver", "download.js"):
        hit = any(key.lower() in s.lower() for s in scripts)
        log(f"  script 含 {key}: {hit}", "ok" if hit else "warn")

    # 优先 gold 主机
    dl_url = ""
    for s in scripts:
        if "download.js" in s.lower():
            dl_url = ("https:" + s) if s.startswith("//") else s
            break
    if not dl_url:
        dl_url = f"https://{_HITOMI_LTN_HOSTS[0]}/download.js"
    info["download_js_url"] = dl_url

    try:
        jr = session.get(dl_url, timeout=30, headers={"Referer": _REFERER})
        info["download_js_ok"] = jr.status_code == 200
        text = jr.text or ""
        log(f"download.js HTTP {jr.status_code} · {len(text)} B", "ok" if jr.ok else "err")
        markers = [
            "download_gallery",
            "JSZip",
            "zip.file",
            "saveAs",
            "url_from_url_from_hash",
            "ajax_download_blob",
            "throttle_interval_ms",
        ]
        found = [m for m in markers if m in text]
        info["download_js_snippets"] = found
        # 客户端 zip 的硬证据：有 JSZip + saveAs + 逐张 url，且无「整包 .zip URL」
        has_client = (
            "JSZip" in text
            and ("saveAs" in text or "generateAsync" in text)
            and "url_from_url_from_hash" in text
        )
        has_server_zip_hint = bool(
            re.search(r"https?://[^\s\"']+\.zip", text)
            or re.search(r"/download/\s*\+|downloadUrl|zip_url", text, re.I)
        )
        info["download_js_is_client_zip"] = has_client and not has_server_zip_hint
        log(f"  关键函数/标记: {', '.join(found) or '(无)'}", "info")
        log(
            f"  判定：客户端 JSZip 打包 = {info['download_js_is_client_zip']}"
            + ("（无服务端整包 zip 线索）" if info["download_js_is_client_zip"] else ""),
            "ok" if info["download_js_is_client_zip"] else "warn",
        )
        # 节选关键行
        for pat in (
            r"function download_gallery[\s\S]{0,200}",
            r"throttle_interval_ms\s*=\s*\d+",
            r"saveAs\([^)]+\)",
            r"zip\.file\([^)]+\)",
        ):
            m = re.search(pat, text)
            if m:
                snip = re.sub(r"\s+", " ", m.group(0))[:160]
                log(f"  >> {snip}", "info")
    except Exception as e:
        log(f"拉 download.js 失败: {e}", "err")

    return info


# ── 阶段 2：探测「服务端整包 ZIP」候选 URL ───────────────────────────────────

def phase_probe_server_zip(session: requests.Session, gid: int, log: _Log) -> list[dict]:
    log("── 阶段 2：探测服务端整包 ZIP URL（预期全 404）──", "info")
    guesses = [
        f"https://hitomi.la/download/{gid}.zip",
        f"https://hitomi.la/galleries/{gid}.zip",
        f"https://hitomi.la/{gid}.zip",
        f"https://ltn.gold-usergeneratedcontent.net/galleries/{gid}.zip",
        f"https://ltn.gold-usergeneratedcontent.net/download/{gid}.zip",
        f"https://a.gold-usergeneratedcontent.net/galleries/{gid}.zip",
        f"https://w1.gold-usergeneratedcontent.net/{gid}.zip",
    ]
    rows = []
    for u in guesses:
        row = {"url": u, "status": 0, "content_type": "", "length": None, "ok_zip": False}
        try:
            r = session.head(u, timeout=20, allow_redirects=True, headers={"Referer": _REFERER})
            # 部分 CDN 对 HEAD 不友好，再 GET 前几字节
            if r.status_code in (405, 501) or (
                r.status_code == 200 and not r.headers.get("Content-Type")
            ):
                r = session.get(
                    u, timeout=20, stream=True, headers={"Referer": _REFERER}
                )
                # 读一点点判断
                chunk = next(r.iter_content(16), b"")
                r.close()
            row["status"] = int(r.status_code)
            row["content_type"] = (r.headers.get("Content-Type") or "")[:80]
            cl = r.headers.get("Content-Length")
            row["length"] = int(cl) if cl and cl.isdigit() else None
            ct = row["content_type"].lower()
            row["ok_zip"] = (
                r.status_code == 200
                and ("zip" in ct or "octet-stream" in ct)
                and (row["length"] is None or row["length"] > 1024)
            )
        except Exception as e:
            row["error"] = str(e)
        rows.append(row)
        flag = "ZIP?" if row["ok_zip"] else "—"
        log(
            f"  {row['status'] or 'ERR':>4} {flag}  {u}"
            + (f"  ({row.get('error')})" if row.get("error") else f"  ct={row['content_type'] or '-'}"),
            "ok" if row["ok_zip"] else "info",
        )
    any_zip = any(r.get("ok_zip") for r in rows)
    log(
        "服务端整包 ZIP：" + ("发现候选！" if any_zip else "未发现（与 download.js 结论一致）"),
        "ok" if not any_zip else "warn",
    )
    return rows


# ── 阶段 3：解析图库 + 官网同款打 zip ────────────────────────────────────────

def phase_parse_gallery(
    session: requests.Session, gid: int, log: _Log
) -> dict:
    log("── 阶段 3：解析 galleryinfo + 拼 webp 直链 ──", "info")
    raw = _fetch_ltn(session, f"/galleries/{gid}.js", log)
    body = raw.strip()
    if "galleryinfo" in body[:80]:
        body = body.split("=", 1)[1].strip().rstrip(";").strip()
    data = json.loads(body)
    title = (data.get("title") or data.get("japanese_title") or f"hitomi_{gid}").strip()
    files = data.get("files") or []
    log(f"标题: {title}", "ok")
    log(f"图片数: {len(files)}", "ok")

    cases, default_m, case_m, b = _load_gg(session, log)
    images = []
    for i, fi in enumerate(files):
        if not isinstance(fi, dict):
            continue
        h = (fi.get("hash") or "").strip()
        if not h:
            continue
        images.append({
            "index": i + 1,
            "hash": h,
            "name": fi.get("name") or f"{i + 1:04d}",
            "zip_name": _zip_entry_name(fi.get("name") or "", i + 1),
            "url": _webp_url(h, cases, default_m, case_m, b),
        })
    log(f"有效直链: {len(images)}", "ok" if images else "err")
    if images:
        log(f"样例 URL: {images[0]['url'][:100]}…", "info")
    return {
        "gallery_id": gid,
        "title": title,
        "japanese_title": (data.get("japanese_title") or "").strip(),
        "images": images,
        "gg": {
            "cases": len(cases),
            "default_m": default_m,
            "case_m": case_m,
            "b": b,
        },
        "_gg_tuple": (cases, default_m, case_m, b),
    }


def _fetch_one(
    session: requests.Session,
    url: str,
    retries: int = 3,
    log: Optional[_Log] = None,
    name: str = "",
) -> bytes:
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            r = session.get(
                url,
                timeout=(20, 120),
                headers={
                    "Referer": _REFERER,
                    "Accept": "image/webp,image/*,*/*;q=0.8",
                },
            )
            r.raise_for_status()
            data = r.content or b""
            ct = (r.headers.get("Content-Type") or "").lower()
            if b"<html" in data[:200].lower() or "text/html" in ct:
                raise RuntimeError("CDN 返回 HTML（缺 Referer 或路由过期）")
            if len(data) < 64:
                raise RuntimeError("文件过小")
            if not _is_webp(data):
                # 允许 jpeg/png 兜底
                if data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n":
                    return bytes(data)
                if data[:4] != b"RIFF":
                    raise RuntimeError(f"非图片 content-type={ct or '?'}")
            return bytes(data)
        except Exception as e:
            last_err = e
            if log and attempt < retries:
                log(f"  ↻ {name} 重试 {attempt}/{retries}: {e}", "warn")
            if attempt < retries:
                time.sleep(0.5 * attempt)
    raise RuntimeError(str(last_err))


def phase_download_zip(
    session: requests.Session,
    gallery: dict,
    out_dir: Path,
    *,
    sample: int = 0,
    delay_ms: int = _OFFICIAL_THROTTLE_MS,
    log: _Log,
) -> dict:
    """官网同款：串行拉 webp + 本地 zip（ZIP_STORED）。"""
    log("── 阶段 4：官网同款打包 ZIP ──", "info")
    images = list(gallery.get("images") or [])
    if sample and sample > 0:
        images = images[:sample]
        log(f"仅抽样前 {len(images)} 张（--sample）", "info")
    if not images:
        raise RuntimeError("无图片可下")

    title = gallery.get("japanese_title") or gallery.get("title") or "hitomi"
    # 官网优先 japanese_title（gallery.js: download_gallery(japanese_title || title)）
    zip_base = _safe_name(title, "hitomi")
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{zip_base}.zip"
    tmp_path = out_dir / f"{zip_base}.zip.part"

    log(f"输出: {zip_path}", "info")
    log(f"间隔: {delay_ms} ms（官网 throttle={_OFFICIAL_THROTTLE_MS}）", "info")
    log(f"共 {len(images)} 张", "info")

    # 下载前再刷 gg
    cases, default_m, case_m, b = _load_gg(session, log)
    for it in images:
        h = it.get("hash") or ""
        if h:
            it["url"] = _webp_url(h, cases, default_m, case_m, b)

    ok_n = fail_n = 0
    failed = []
    t0 = time.time()
    if tmp_path.is_file():
        try:
            tmp_path.unlink()
        except Exception:
            pass

    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_STORED) as zf:
        used = set()
        for i, it in enumerate(images):
            name = it.get("zip_name") or f"{i + 1:04d}.webp"
            zn = name
            n = 1
            while zn.lower() in used:
                stem, ext = os.path.splitext(name)
                zn = f"{stem}_{n}{ext or '.webp'}"
                n += 1
            used.add(zn.lower())

            if i > 0 and delay_ms > 0:
                time.sleep(delay_ms / 1000.0)

            try:
                data = _fetch_one(
                    session, it["url"], retries=3, log=log, name=zn
                )
                if _is_webp(data) and not zn.lower().endswith(".webp"):
                    zn = os.path.splitext(zn)[0] + ".webp"
                zf.writestr(zn, data)
                ok_n += 1
                if (i + 1) % 5 == 0 or i + 1 == total_safe(len(images)):
                    log(f"  ✓ {i + 1}/{len(images)}  {zn}  ({len(data)} B)", "ok")
            except Exception as e:
                fail_n += 1
                failed.append({"name": zn, "url": it.get("url"), "error": str(e)})
                log(f"  ✗ {i + 1}/{len(images)}  {zn}: {e}", "err")

    elapsed = time.time() - t0
    if ok_n <= 0:
        try:
            tmp_path.unlink()
        except Exception:
            pass
        raise RuntimeError(f"全部失败（{fail_n}），未生成 zip")

    # 有失败仍产出 zip（与「能下多少算多少」一致；正式程序可更严）
    if zip_path.is_file():
        try:
            zip_path.unlink()
        except Exception:
            pass
    tmp_path.replace(zip_path)
    size = zip_path.stat().st_size
    log(
        f"完成: {zip_path.name} · {size} B · 成功 {ok_n} · 失败 {fail_n} · {elapsed:.1f}s",
        "ok" if fail_n == 0 else "warn",
    )
    return {
        "zip_path": str(zip_path),
        "bytes": size,
        "ok_n": ok_n,
        "fail_n": fail_n,
        "failed": failed,
        "elapsed_s": round(elapsed, 2),
        "sample": sample,
    }


def total_safe(n: int) -> int:
    return n


# ── 写报告 ───────────────────────────────────────────────────────────────────

def write_report(
    path: Path,
    *,
    gid: int,
    input_text: str,
    page_info: dict,
    server_probe: list,
    gallery: dict,
    dl_result: Optional[dict],
    log: _Log,
):
    lines = [
        "Hitomi.la ZIP 下载 Debug 报告",
        f"time: {datetime.now().isoformat(timespec='seconds')}",
        f"input: {input_text}",
        f"gallery_id: {gid}",
        "",
        "═══ 结论摘要 ═══",
        f"作品页 Download 按钮: {page_info.get('has_dl_button')}",
        f"download.js 为客户端 JSZip: {page_info.get('download_js_is_client_zip')}",
        f"服务端整包 ZIP 候选命中: {sum(1 for r in server_probe if r.get('ok_zip'))}",
        f"图集标题: {gallery.get('title')}",
        f"图片数: {len(gallery.get('images') or [])}",
        f"实测 zip: {(dl_result or {}).get('zip_path') or '(未下载)'}",
        f"zip 成功/失败: {(dl_result or {}).get('ok_n', '-')}/{(dl_result or {}).get('fail_n', '-')}",
        "",
        "说明: 官网「Download」= 浏览器内逐张拉 webp + JSZip 打包，",
        "      并非服务器返回现成 .zip。主程序应复刻此流程（已有 HitomiZipDownloadWorker）。",
        "",
        "═══ 阶段 1 页面/脚本 ═══",
        json.dumps({k: v for k, v in page_info.items() if k != "scripts"}, ensure_ascii=False, indent=2),
        "",
        "scripts:",
        *[f"  {s}" for s in (page_info.get("scripts") or [])],
        "",
        "═══ 阶段 2 服务端 ZIP 探测 ═══",
        json.dumps(server_probe, ensure_ascii=False, indent=2),
        "",
        "═══ 阶段 3 图库 ═══",
        f"title: {gallery.get('title')}",
        f"japanese_title: {gallery.get('japanese_title')}",
        f"gg: {gallery.get('gg')}",
        f"images: {len(gallery.get('images') or [])}",
    ]
    if gallery.get("images"):
        lines.append(f"sample_url: {gallery['images'][0].get('url')}")
    lines += [
        "",
        "═══ 阶段 4 下载 ═══",
        json.dumps(dl_result or {"skipped": True}, ensure_ascii=False, indent=2),
        "",
        "═══ log ═══",
        *log.lines,
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    log(f"报告已写: {path}", "ok")


# ── main ─────────────────────────────────────────────────────────────────────

def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(
        description="hitomi.la 官网 Download / ZIP 机制 Debug（含实测打包）"
    )
    ap.add_argument("url_or_id", help="图库链接或数字 ID")
    ap.add_argument(
        "--no-download",
        action="store_true",
        help="只做页面/接口探测，不真正下载 zip",
    )
    ap.add_argument(
        "--sample",
        type=int,
        default=0,
        help="只下载前 N 张做烟测（0=全部）",
    )
    ap.add_argument(
        "--delay-ms",
        type=int,
        default=_OFFICIAL_THROTTLE_MS,
        help=f"张间间隔毫秒（官网默认 {_OFFICIAL_THROTTLE_MS}）",
    )
    ap.add_argument(
        "--out",
        type=str,
        default="",
        help="zip 输出目录（默认 data/）",
    )
    args = ap.parse_args(argv)

    log = _Log()
    log("hitomi.la ZIP Debug 启动", "info")
    log(f"输入: {args.url_or_id}", "info")

    try:
        gid = extract_hitomi_id(args.url_or_id)
    except Exception as e:
        log(f"解析 ID 失败: {e}", "err")
        return 2

    log(f"gallery_id = {gid}", "ok")
    session = _make_session()
    records = _ROOT / "data"
    records.mkdir(exist_ok=True)
    out_dir = Path(args.out) if args.out else records

    page_info = phase_page_and_scripts(session, gid, log)
    server_probe = phase_probe_server_zip(session, gid, log)

    try:
        gallery = phase_parse_gallery(session, gid, log)
    except Exception as e:
        log(f"解析图库失败: {e}", "err")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        write_report(
            records / f"hitomi_zip_debug_{gid}_{stamp}.txt",
            gid=gid,
            input_text=args.url_or_id,
            page_info=page_info,
            server_probe=server_probe,
            gallery={"title": "", "images": [], "gg": {}},
            dl_result=None,
            log=log,
        )
        return 1

    dl_result = None
    if not args.no_download:
        try:
            dl_result = phase_download_zip(
                session,
                gallery,
                out_dir,
                sample=max(0, int(args.sample or 0)),
                delay_ms=max(0, int(args.delay_ms)),
                log=log,
            )
        except Exception as e:
            log(f"下载/打包失败: {e}", "err")
            dl_result = {"error": str(e)}
    else:
        log("已跳过下载（--no-download）", "info")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = records / f"hitomi_zip_debug_{gid}_{stamp}.txt"
    # gallery 里的 _gg_tuple 不能 json
    gallery_public = {k: v for k, v in gallery.items() if not k.startswith("_")}
    write_report(
        report_path,
        gid=gid,
        input_text=args.url_or_id,
        page_info=page_info,
        server_probe=server_probe,
        gallery=gallery_public,
        dl_result=dl_result,
        log=log,
    )

    log("", "info")
    log("════ 最终结论 ════", "info")
    if page_info.get("download_js_is_client_zip"):
        log(
            "官网 Download = 客户端逐张 webp + JSZip → 浏览器弹出 .zip；"
            "没有可用的服务端整包 ZIP URL。",
            "ok",
        )
        log(
            "主程序应继续用「解析 files → 拉 webp → 本地打 zip」"
            "（现有 HitomiZipDownloadWorker 已是此路径）。",
            "ok",
        )
    else:
        log("未能确认 download.js 机制，请查看报告。", "warn")
    if dl_result and dl_result.get("zip_path"):
        log(f"实测 zip: {dl_result['zip_path']}", "ok")
    return 0 if (args.no_download or (dl_result and dl_result.get("zip_path"))) else 1


if __name__ == "__main__":
    sys.exit(main())
