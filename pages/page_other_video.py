# pages/page_other_video.py
# 「其它视频」：即梦 / LibTV / XVideos / Pornhub 共用一套解析→画质卡→下载流程。
# XVideos 默认勾选最高画质（HLS 优先）；HLS 逐片下载后 ffmpeg 无损封装 mp4。
# Pornhub 的 HLS 两种分片格式都遇到过（见 _fetch_hls）：.m4s 走 fMP4 直拼，
# .ts 走 ffmpeg 封装。
# 白名单在系统总览里是一条「其它视频」，本页按链接识别平台。

from __future__ import annotations

import os
import subprocess
import time

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QTextEdit, QVBoxLayout, QWidget,
)

from pages.page_jimeng import (
    PageJimeng,
    DownloadWorker as _JimengDownloadWorker,
    extract_jimeng_url,
    looks_like_jimeng,
    UA_BROWSER,
    JIMENG_REFERER,
    _parse_work_detail,
    build_media_specs,
    _safe_filename_part,
    HAS_REQUESTS,
    req_lib,
)
from pages.page_liblib import (
    extract_liblib_url,
    looks_like_liblib,
    parse_liblib_detail,
    LIBLIB_REFERER,
)
from pages.page_douyin import (
    MediaItem,
    is_qishui_url,
    extract_url as extract_douyin_family_url,
    _ffmpeg_extract_audio,
)
from styles.style_all import (
    CARD_LEFT_GAP,
    CARD_RIGHT_GAP,
    MEDIUM_BUTTON_H,
    apply_mini_button,
    message_box_info,
    message_box_warn,
    tk,
)
from utils.logger import get_logger
from utils.cursor_toast import (
    in_batch_download, set_batch_progress_note, show_cursor_toast,
)
from utils.other_video_records import (
    PLAT_ORDER,
    active_names,
    name_aliases,
    add_records,
    classify_label,
    format_entries,
    hint_text,
    load_entries,
    merge_external,
    parse_entries,
    read_external,
    save_entries,
    scan_dir,
)
from utils.gallery_records import records_path as _gallery_records_path
from utils.records_dialog_ui import RecordsTotalBar
from utils.download_watchdog import StalledDownloadError, find_socket
from utils.xvideos import (
    looks_like_xvideos,
    parse_xvideos,
    pick_same_quality,
    is_hls_url,
    hls_segments,
    hls_is_fmp4,
)
from utils.pornhub import (
    looks_like_pornhub,
    parse_pornhub,
)

_log = get_logger(__name__)


def detect_other_platform(text: str) -> str:
    """识别其它视频平台。汽水已拆到音频下载，这里不再认。"""
    t = (text or "").strip()
    if not t:
        return ""
    if is_qishui_url(t):
        return ""
    try:
        u = extract_douyin_family_url(t)
        if u and is_qishui_url(u):
            return ""
    except Exception:
        pass
    if looks_like_xvideos(t):
        return "xvideos"
    if looks_like_pornhub(t):
        return "pornhub"
    if looks_like_jimeng(t) or extract_jimeng_url(t):
        return "jimeng"
    if looks_like_liblib(t) or extract_liblib_url(t):
        return "liblib"
    return ""


def _fetch_html(url: str, referer: str) -> str:
    headers = {
        "User-Agent": UA_BROWSER,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": referer,
    }
    if HAS_REQUESTS:
        r = req_lib.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or r.encoding or "utf-8"
        return r.text or ""
    import urllib.request
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


class _HlsExpired(Exception):
    """分片 / 清单返回 403、410：签名令牌过期。"""


def _unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 1
    while os.path.exists(f"{base}_{n}{ext}"):
        n += 1
    return f"{base}_{n}{ext}"


class OtherParseWorker(QThread):
    ok = pyqtSignal(object, list)
    err = pyqtSignal(str)
    log = pyqtSignal(str, str)

    def __init__(self, url_text: str, cookie_path: str = ""):
        super().__init__()
        self.url_text = url_text or ""
        self.cookie_path = cookie_path or ""

    def _emit(self, msg: str, level: str = "info"):
        self.log.emit(msg, level)

    def _cookie_str(self) -> str:
        p = (self.cookie_path or "").strip()
        if not p or not os.path.isfile(p):
            return ""
        try:
            from pages.page_douyin import load_cookies
            s, _d = load_cookies(p)
            return s or ""
        except Exception:
            return ""

    def run(self):
        try:
            text = (self.url_text or "").strip()
            plat = detect_other_platform(text)
            if plat == "jimeng":
                url = extract_jimeng_url(text) or text
                self._emit(f"识别即梦链接：{url}")
                self._emit("拉取作品页 HTML…")
                body = _fetch_html(url, JIMENG_REFERER)
                value = _parse_work_detail(body)
                info, items = build_media_specs(value)
                info["platform"] = "jimeng"
                info["referer"] = JIMENG_REFERER
                self._emit(
                    f"解析成功：{info.get('author')} · {info.get('title')} · {len(items)} 档画质",
                    "ok",
                )
                self.ok.emit(info, items)
                return
            if plat == "liblib":
                url = extract_liblib_url(text) or text
                self._emit(f"识别 LibTV 链接：{url}")
                self._emit("拉取作品页 HTML…")
                body = _fetch_html(url, LIBLIB_REFERER)
                info, items = parse_liblib_detail(body)
                info["platform"] = "liblib"
                info["referer"] = LIBLIB_REFERER
                self._emit(
                    f"解析成功：{info.get('author')} · {info.get('title')} · {len(items)} 项媒体",
                    "ok",
                )
                self.ok.emit(info, items)
                return
            if plat == "xvideos":
                self._emit("识别为 XVideos")
                info, specs = parse_xvideos(
                    text, log_cb=lambda m, lv="info": self._emit(m, lv),
                )
                cover = info.get("cover") or ""
                items = [
                    MediaItem(
                        "video", s["label"], s["url"], s.get("ext") or ".mp4",
                        cover, i, default_checked=(i == 0),  # 最高画质默认勾选
                    )
                    for i, s in enumerate(specs)
                ]
                dur = int(info.get("duration") or 0)
                dtxt = f" · {dur // 60}:{dur % 60:02d}" if dur else ""
                self._emit(
                    f"解析成功：{info.get('author')} · {str(info.get('title') or '')[:40]}"
                    f"{dtxt} · {len(items)} 档画质",
                    "ok",
                )
                self.ok.emit(info, items)
                return
            if plat == "pornhub":
                self._emit("识别为 Pornhub")
                info, specs = parse_pornhub(
                    text, log_cb=lambda m, lv="info": self._emit(m, lv),
                )
                cover = info.get("cover") or ""
                items = [
                    MediaItem(
                        "video", s["label"], s["url"], s.get("ext") or ".mp4",
                        cover, i, default_checked=(i == 0),  # 最高画质默认勾选
                    )
                    for i, s in enumerate(specs)
                ]
                dur = int(info.get("duration") or 0)
                dtxt = f" · {dur // 60}:{dur % 60:02d}" if dur else ""
                self._emit(
                    f"解析成功：{info.get('author')} · {str(info.get('title') or '')[:40]}"
                    f"{dtxt} · {len(items)} 档画质",
                    "ok",
                )
                self.ok.emit(info, items)
                return
            raise ValueError(
                "不是即梦 / LibTV / XVideos / Pornhub 链接。\n"
                "支持 jimeng.jianying.com、liblib.tv/detail/、xvideos.com/video…、"
                "pornhub.com/view_video.php?viewkey=…\n"
                "汽水音乐请到「音频下载」。"
            )
        except Exception as e:
            _log.exception("其它视频解析失败")
            self.err.emit(str(e) or repr(e))


class OtherDownloadWorker(_JimengDownloadWorker):
    """即梦下载器 + 每条独立 Referer + 汽水音频抽取 + XVideos HLS 分片下载。"""

    def run(self):
        from utils.download_watchdog import DownloadWatchdog

        wd = self._wd = DownloadWatchdog()
        stall_msg = f"{int(wd.stall_seconds)} 秒无响应，已视为无法下载"
        self._saved_records = []
        os.makedirs(self._save_dir, exist_ok=True)
        total = len(self._jobs)
        ok_n = 0
        if total <= 0:
            self.done.emit(self._save_dir, 0)
            return

        for i, job in enumerate(self._jobs):
            if self._cancel and self._cancel[0]:
                self.cancelled.emit()
                return
            label = job.get("label") or f"视频{i+1}"
            url = job.get("url") or ""
            filename = job.get("filename") or f"video_{i+1}.mp4"
            referer = job.get("referer") or JIMENG_REFERER
            extract_audio = bool(job.get("extract_audio"))
            headers = {
                "User-Agent": UA_BROWSER,
                "Referer": referer,
                "Accept": "*/*",
            }
            path = os.path.join(self._save_dir, filename)
            if os.path.exists(path):
                base, ext = os.path.splitext(filename)
                n = 1
                while True:
                    cand = f"{base}_{n}{ext}"
                    path2 = os.path.join(self._save_dir, cand)
                    if not os.path.exists(path2):
                        path = path2
                        filename = cand
                        break
                    n += 1

            self._emit(f"下载 {label} → {filename}")
            key = f"other-{i}"
            try:
                dl_path = path
                dl_name = filename
                if extract_audio:
                    base, _ext = os.path.splitext(path)
                    dl_path = base + ".mp4"
                    dl_name = os.path.basename(dl_path)
                final_path = dl_path
                if is_hls_url(url) and not extract_audio:
                    size, final_path = self._fetch_hls(
                        job, url, dl_path, key, headers, wd, stall_msg,
                        job_index=i, job_total=total,
                    )
                else:
                    size = self._fetch_direct(
                        job, url, dl_path, key, headers, wd, stall_msg,
                        job_index=i, job_total=total,
                    )
                if size and size > 0:
                    if extract_audio:
                        conv, outp = _ffmpeg_extract_audio(dl_path, os.path.splitext(path)[1] or ".mp3")
                        try:
                            if os.path.isfile(dl_path) and (
                                not conv or os.path.normcase(dl_path) != os.path.normcase(outp or "")
                            ):
                                os.remove(dl_path)
                        except Exception:
                            pass
                        if not conv:
                            self._emit(f"✗ {label} · {outp}", "err")
                        else:
                            ok_n += 1
                            self._remember_record(job)
                            self._emit(f"✓ {label} · {os.path.basename(outp)}", "ok")
                    else:
                        ok_n += 1
                        self._remember_record(job)
                        self._emit(f"✓ {label} · {os.path.basename(final_path)}", "ok")
                else:
                    self._emit(f"✗ {label} · 空文件", "err")
                    try:
                        if os.path.isfile(dl_path) and os.path.getsize(dl_path) == 0:
                            os.remove(dl_path)
                    except Exception:
                        pass
            except InterruptedError:
                self.cancelled.emit()
                return
            except StalledDownloadError as e:
                self._emit(f"✗ {label} · {e}", "err")
            except Exception as e:
                self._emit(f"✗ {label} · {e}", "err")
            pct = int(round((i + 1) * 100.0 / total))
            self.progress.emit(pct)

        self.done.emit(self._save_dir, ok_n)

    def _remember_record(self, job):
        """这一档下成了：记下分类后的正式文件名，供页面写入已下记录。"""
        rec = job.get("record") if isinstance(job, dict) else None
        if not isinstance(rec, dict):
            return
        name = (rec.get("name") or "").strip()
        if not name:
            return
        self._saved_records.append(dict(rec))

    # ── 令牌过期重解析 / 直链 / HLS ─────────────────────────────
    # XVideos 和 Pornhub 的直链都带时效令牌（Pornhub 约 2 小时），
    # 403/410 时回视频页重新解析，拿同一档画质的新地址。
    def _refresh_url(self, job) -> str:
        """地址过期（403/410）：重新解析视频页，取同一档画质的新地址。"""
        page_url = (job.get("page_url") or "").strip()
        if not page_url:
            raise RuntimeError("地址已失效（HTTP 403），无法重新解析")
        plat = (job.get("platform") or "").strip()
        self._emit("地址已过期，正在重新解析视频页…", "warn")
        if plat == "pornhub" or looks_like_pornhub(page_url):
            _info, specs = parse_pornhub(page_url)
        elif looks_like_xvideos(page_url):
            _info, specs = parse_xvideos(page_url)
        else:
            raise RuntimeError("地址已失效（HTTP 403），无法重新解析")
        spec = pick_same_quality(specs, job.get("label") or "")
        if not spec or not spec.get("url"):
            raise RuntimeError("重新解析后找不到同一档画质")
        job["url"] = spec["url"]
        job["_xv_refreshed"] = True
        return spec["url"]

    def _can_refresh(self, job) -> bool:
        pu = (job.get("page_url") or "").strip()
        if not pu or job.get("_xv_refreshed"):
            return False
        return looks_like_xvideos(pu) or looks_like_pornhub(pu)

    def _fetch_direct(self, job, url, path, key, headers, wd, stall_msg,
                      job_index: int = 0, job_total: int = 1) -> int:
        try:
            return self._fetch(
                url, path, key, headers, wd, stall_msg,
                job_index=job_index, job_total=job_total,
            )
        except Exception as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (403, 410) and self._can_refresh(job):
                url = self._refresh_url(job)
                return self._fetch(
                    url, path, key, headers, wd, stall_msg,
                    job_index=job_index, job_total=job_total,
                )
            raise

    def _hls_playlist(self, url, headers):
        """→ (分片绝对地址, 是否 fMP4)。fMP4 的分片表首项是 EXT-X-MAP 初始化段。"""
        r = req_lib.get(url, headers=headers, timeout=30)
        if r.status_code in (403, 410):
            raise _HlsExpired()
        r.raise_for_status()
        text = r.text or ""
        return hls_segments(text, r.url or url), hls_is_fmp4(text)

    def _hls_get_segment(self, url, headers, key, wd, stall_msg) -> bytes:
        """单个分片读入内存；网络错误最多试 3 次。403/410 交给上层重解析。"""
        last = None
        for attempt in range(3):
            if self._cancel and self._cancel[0]:
                raise InterruptedError()
            if wd.is_dead(key):
                raise StalledDownloadError(stall_msg)
            try:
                r = req_lib.get(url, headers=headers, stream=True, timeout=(15, 30))
                if r.status_code in (403, 410):
                    r.close()
                    raise _HlsExpired()
                r.raise_for_status()
                wd.attach_socket(key, find_socket(r))
                buf = bytearray()
                for chunk in r.iter_content(65536):
                    if self._cancel and self._cancel[0]:
                        raise InterruptedError()
                    if wd.is_dead(key):
                        raise StalledDownloadError(stall_msg)
                    if chunk:
                        buf += chunk
                        wd.touch(key)
                if not buf:
                    raise RuntimeError("空分片")
                return bytes(buf)
            except (InterruptedError, StalledDownloadError, _HlsExpired):
                raise
            except Exception as e:
                last = e
                if wd.is_dead(key):
                    raise StalledDownloadError(stall_msg)
                time.sleep(1.0 + attempt)
        raise RuntimeError(f"分片下载失败（已重试 3 次）：{last}")

    def _fetch_hls(self, job, url, out_path, key, headers, wd, stall_msg,
                   job_index: int = 0, job_total: int = 1):
        """HLS：逐片下载再拼成整段。返回 (字节数, 最终路径)。

        分两种分片格式（Pornhub 的 CDN 两种都发过）：
          · fMP4（.m4s + EXT-X-MAP 初始化段）→ init 在前直接顺序拼接，
            结果本身就是合法 mp4，不需要 ffmpeg。
          · MPEG-TS（.ts）→ 拼成 .ts，再 ffmpeg -c copy 封装成 mp4。
        """
        if not HAS_REQUESTS:
            raise RuntimeError("缺少 requests 库，无法下载 HLS 视频")
        tmp = os.path.splitext(out_path)[0] + ".part.ts"
        wd.begin(key)
        try:
            try:
                segs, is_fmp4 = self._hls_playlist(url, headers)
            except _HlsExpired:
                if not self._can_refresh(job):
                    raise RuntimeError("地址已失效（HTTP 403）")
                url = self._refresh_url(job)
                try:
                    segs, is_fmp4 = self._hls_playlist(url, headers)
                except _HlsExpired:
                    raise RuntimeError("重新解析后地址仍被拒绝（HTTP 403）")
            if is_fmp4:
                tmp = os.path.splitext(out_path)[0] + ".part.mp4"
            n = len(segs)
            self._emit(
                f"HLS 共 {n} 个分片（{'fMP4' if is_fmp4 else 'MPEG-TS'}）"
            )
            written = 0
            k = 0
            with open(tmp, "wb") as f:
                while k < n:
                    if self._cancel and self._cancel[0]:
                        raise InterruptedError()
                    if wd.is_dead(key):
                        raise StalledDownloadError(stall_msg)
                    try:
                        data = self._hls_get_segment(segs[k], headers, key, wd, stall_msg)
                    except _HlsExpired:
                        if not self._can_refresh(job):
                            raise RuntimeError("分片地址已失效（HTTP 403）")
                        url = self._refresh_url(job)
                        try:
                            new_segs, new_fmp4 = self._hls_playlist(url, headers)
                        except _HlsExpired:
                            raise RuntimeError("重新解析后地址仍被拒绝（HTTP 403）")
                        if new_fmp4 != is_fmp4:
                            # 同一档画质换了分片格式，继续拼会得到坏文件。
                            # 地址已刷新，重下即可拿到正确格式。
                            raise RuntimeError(
                                "重新解析后分片格式变了（fMP4↔MPEG-TS），"
                                "已中止本次下载，请重新下载该画质"
                            )
                        if len(new_segs) != n:
                            self._emit("分片数量有变化，从头重新下载", "warn")
                            f.seek(0)
                            f.truncate()
                            written = 0
                            k = 0
                            n = len(new_segs)
                        segs = new_segs
                        continue
                    f.write(data)
                    written += len(data)
                    k += 1
                    wd.touch(key)
                    self._emit_job_progress(job_index, job_total, k, n)
            if written <= 0:
                return 0, out_path
            if is_fmp4:
                final = _unique_path(os.path.splitext(out_path)[0] + ".mp4")
                os.replace(tmp, final)
                return written, final
            return written, self._hls_finalize(tmp, out_path)
        finally:
            wd.end(key)
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    def _hls_finalize(self, tmp, out_path) -> str:
        """.ts → .mp4（-c copy）；没有 ffmpeg 或封装失败则保留 .ts。"""
        ff = None
        try:
            from utils.region_recorder import find_ffmpeg
            ff = find_ffmpeg()
        except Exception:
            ff = None
        mp4 = os.path.splitext(out_path)[0] + ".mp4"
        if ff:
            kw = {}
            if os.name == "nt":
                kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            try:
                p = subprocess.run(
                    [ff, "-y", "-loglevel", "error", "-i", tmp,
                     "-c", "copy", "-bsf:a", "aac_adtstoasc", mp4],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    timeout=1800, **kw,
                )
                if p.returncode == 0 and os.path.isfile(mp4) and os.path.getsize(mp4) > 0:
                    return mp4
                err = (p.stderr or b"").decode("utf-8", "replace").strip()[-300:]
                self._emit(f"ffmpeg 封装 mp4 失败，保留 .ts：{err or p.returncode}", "warn")
            except Exception as e:
                self._emit(f"ffmpeg 封装 mp4 失败，保留 .ts：{e}", "warn")
            try:
                if os.path.isfile(mp4):
                    os.remove(mp4)
            except Exception:
                pass
        else:
            self._emit("未检测到 ffmpeg，保留为 .ts（播放器可直接播放）", "warn")
        ts = _unique_path(os.path.splitext(out_path)[0] + ".ts")
        os.replace(tmp, ts)
        return ts


_RECORD_SKIP_YELLOW = "#eab308"
_RECORD_SKIP_TIP = (
    "开：按平台、类型、画质查已下记录，命中的那一档跳过\n"
    "关：不查记录，照常下载；磁盘上的同名文件仍会自动改名"
)
_RECORD_PLACEHOLDER = (
    "# 即梦\n"
    "[视频 · 原片]\n"
    "[桌面助手][即梦]作者_标题_原片.mp4\n"
    "[视频 · 1080p]\n"
    "[桌面助手][即梦]作者_标题_1080p.mp4\n"
    "\n"
    "# LibTV\n"
    "[成片]\n"
    "[桌面助手][LibTV]作者_标题_成片.mp4\n"
    "\n"
    "# XVideos\n"
    "[HLS · 1080p]\n"
    "[桌面助手][XVideos]作者_标题_1080p.mp4\n"
    "[直链 · MP4 高]\n"
    "[桌面助手][XVideos]作者_标题_MP4 高.mp4\n"
    "\n"
    "# Pornhub\n"
    "[HLS · 720p]\n"
    "[桌面助手][Pornhub]作者_标题_720p.mp4\n"
)


class PageOtherVideo(PageJimeng):
    """其它视频子页：一套 UI，按链接区分即梦 / LibTV / XVideos / Pornhub。

    已下记录和预下载一样分成这四段，段内再按类型和画质分开，只跳过命中的那一档。
    """

    def __init__(self):
        super().__init__()
        self._current_platform = ""
        try:
            self.url_edit.setPlaceholderText(
                "粘贴即梦 / LibTV / XVideos / Pornhub 链接 → 回车自动解析下载"
            )
        except Exception:
            pass
        try:
            self.lbl_batch_hint.setText("粘贴即梦、LibTV、XVideos 或 Pornhub 链接后按回车开始解析")
        except Exception:
            pass
        lay = self.layout()
        if lay is not None:
            lay.addWidget(self._build_record_bar())
        self._refresh_record_hint()

    def _make_empty_card(self):
        card = super()._make_empty_card()
        from PyQt5.QtWidgets import QLabel
        for w in card.findChildren(QLabel):
            w.setText("粘贴即梦 / LibTV / XVideos /\nPornhub 链接 → 回车")
            break
        return card

    def _clipboard_url(self) -> str:
        text = self._clipboard_raw()
        if not text:
            return ""
        if detect_other_platform(text):
            return text
        return ""

    @staticmethod
    def _looks_like_douyin(text: str) -> bool:
        t = (text or "").lower()
        if is_qishui_url(t):
            return False
        return ("douyin.com" in t) or ("tiktok.com" in t) or ("iesdouyin.com" in t)

    def _start_flow(self, use_clipboard=True):
        text = ""
        raw_clip = self._clipboard_raw() if use_clipboard else ""
        if use_clipboard:
            text = self._clipboard_url()
            if text:
                self.url_edit.setText(text)
                self._log("已从剪贴板读取链接", "ok")
        if not text:
            text = self.url_edit.text().strip()

        probe = text or raw_clip
        if probe and (is_qishui_url(probe) or is_qishui_url(text or "")):
            self._log("检测到汽水音乐链接，已切换到「音频下载」…", "ok")
            try:
                from pages.page_audio import open_qishui_page
                if not open_qishui_page(self, text or raw_clip, auto_start=True):
                    self._log("请从侧栏打开「音频下载」后再粘贴解析", "warn")
            except Exception:
                _log.exception("从其它视频切换到音频下载失败")
                self._log("请从侧栏打开「音频下载」后再粘贴解析", "warn")
            return
        plat = detect_other_platform(probe)

        if probe and self._looks_like_douyin(probe) and not plat:
            pv = self._find_page_video()
            if pv is not None and hasattr(pv, "handoff_to_douyin"):
                self._log("检测到抖音/TikTok 链接，已切换到「抖音」分页…", "ok")
                pv.handoff_to_douyin(text or raw_clip, auto_start=True)
            return
        if probe and self._looks_like_bilibili(probe) and not plat:
            pv = self._find_page_video()
            if pv is not None and hasattr(pv, "handoff_to_bilibili"):
                self._log("检测到 B站 链接，已切换到「B站」分页…", "ok")
                pv.handoff_to_bilibili(text or raw_clip, auto_start=True)
            return
        if probe and self._looks_like_youtube(probe) and not plat:
            pv = self._find_page_video()
            if pv is not None and hasattr(pv, "handoff_to_youtube"):
                self._log("检测到 YouTube 链接，已切换到「YouTube」分页…", "ok")
                pv.handoff_to_youtube(text or raw_clip, auto_start=True)
            return

        if not text:
            self._log("剪贴板里没有即梦 / LibTV / XVideos / Pornhub 链接，输入框也是空的", "warn")
            self._set_status("没有链接", "#f97316")
            return

        self._current_platform = plat or detect_other_platform(text) or ""
        self._auto_dl = True
        self._parse(text)

    def _parse(self, url_text=None):
        if self._parse_worker and self._parse_worker.isRunning():
            self._log("正在解析中，请稍候…", "warn")
            return
        if self._dl_worker and self._dl_worker.isRunning():
            self._log("正在下载中，请先等待或取消", "warn")
            return
        if not isinstance(url_text, str) or not url_text.strip():
            url_text = self.url_edit.text()
        url_text = (url_text or "").strip()
        if not url_text:
            self._log("请先粘贴视频链接", "warn")
            return

        self.btn_dl_sel.setEnabled(False)
        self.progress.setValue(0)
        self._set_status("解析中…", "#f97316")
        self._log("开始解析…")
        self._clear_cards()
        self._emit_nav_progress(True, mode="busy")
        ck = ""
        try:
            ck = (self.ck_path.text() or "").strip()
        except Exception:
            ck = ""
        self._parse_worker = OtherParseWorker(url_text, ck)
        self._parse_worker.ok.connect(self._on_parse_ok)
        self._parse_worker.err.connect(self._on_parse_err)
        self._parse_worker.log.connect(self._log)
        self._parse_worker.start()

    def _on_parse_ok(self, info: dict, items: list):
        self._info = info or {}
        self._current_platform = str((self._info or {}).get("platform") or self._current_platform or "")
        super()._on_parse_ok(info, items)

    def _make_filename(self, item) -> str:
        info = self._info or {}
        plat = str(info.get("platform") or self._current_platform or "video")
        author = _safe_filename_part(info.get("author") or "", 24)
        title = _safe_filename_part(info.get("title") or "", 36)
        label = getattr(item, "label", None) or (item if isinstance(item, str) else "video")
        qual = _safe_filename_part(str(label).split("·")[0].strip() or "video", 12)
        ext = getattr(item, "ext", None) or ".mp4"
        if not str(ext).startswith("."):
            ext = "." + str(ext)
        tag = {"jimeng": "即梦", "liblib": "LibTV", "qishui": "汽水",
              "xvideos": "XVideos", "pornhub": "Pornhub"}.get(plat, plat)
        return f"[桌面助手][{tag}]{author}_{title}_{qual}{ext}"

    def _download_selected(self):
        jobs = []
        referer = (self._info or {}).get("referer") or ""
        page_url = (self._info or {}).get("page_url") or ""
        plat = str((self._info or {}).get("platform") or self._current_platform or "")
        for c in self._cards:
            if not c.is_checked():
                continue
            it = c.item
            filename = self._make_filename(it)
            label = it.label or "视频"
            tag, kind, qual = classify_label(plat, label)
            if getattr(it, "extract_audio", False):
                kind = "音频"
            jobs.append({
                "label": label,
                "url": it.url,
                "filename": filename,
                "referer": referer,
                "extract_audio": bool(getattr(it, "extract_audio", False)),
                "page_url": page_url,
                # 令牌过期重解析时要按平台走各自的解析器
                "platform": plat,
                "record": {
                    "platform": tag,
                    "kind": kind,
                    "quality": qual,
                    "name": filename,
                    "suspect": False,
                },
            })
        if not jobs:
            self._log("请先勾选至少一项", "warn")
            self._set_status("未勾选媒体", "#f97316")
            if self._auto_dl:
                self._auto_dl = False
            if in_batch_download(self):
                self._last_download_ok = False
                self._last_batch_note = "失败"
                self._emit_nav_progress(False)
                self._report_batch_item_done()
            return
        kept, skipped = self._partition_recorded(jobs)
        if skipped and not kept:
            self._mark_already_downloaded(skipped)
            return
        if skipped:
            self._log(
                "记录中已有，跳过 %d 档：%s"
                % (len(skipped), "、".join(self._record_brief(j) for j in skipped)),
                "ok",
            )
        elif kept and not self._record_skip_enabled():
            known_names = getattr(self, "_record_known", set())
            known = [
                j for j in kept
                if name_aliases((j.get("record") or {}).get("name") or "") & known_names
            ]
            if known:
                self._log(
                    "记录中已有 %d 档，防重已关，继续：%s"
                    % (len(known), "、".join(self._record_brief(j) for j in known)),
                    "warn",
                )
        self._start_download(kept)

    def _start_download(self, jobs: list):
        if self._dl_worker and self._dl_worker.isRunning():
            self._log("正在下载中，请稍候…", "warn")
            return
        save_dir = (self.save_edit.text() or "").strip() or os.path.expanduser("~/Downloads")
        try:
            os.makedirs(save_dir, exist_ok=True)
        except Exception as e:
            self._log(f"无法创建保存目录：{e}", "err")
            self._last_download_ok = False
            self._last_batch_note = "失败"
            self._report_batch_item_done()
            return

        self._cancel_flag[0] = False
        self._dl_total = len(jobs)
        self._last_download_ok = False
        self._last_batch_note = ""
        self.btn_dl_sel.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setValue(0)
        self._set_status(f"准备下载 {len(jobs)} 项…", "#f97316")
        self._emit_nav_progress(True, 0, mode="progress")

        self._dl_worker = OtherDownloadWorker(jobs, save_dir, self._cancel_flag)
        self._dl_worker.progress.connect(self._on_dl_progress)
        self._dl_worker.log.connect(self._log)
        self._dl_worker.done.connect(self._on_dl_done)
        self._dl_worker.cancelled.connect(self._on_dl_cancel)
        self._dl_worker.start()

    def _report_batch_item_done(self):
        w = self
        src = (self._current_platform or "jimeng").strip()
        try:
            while w is not None:
                if hasattr(w, "notify_batch_item_done") and getattr(w, "_batch_mode", False):
                    w.notify_batch_item_done(
                        bool(getattr(self, "_last_download_ok", False)),
                        note=(getattr(self, "_last_batch_note", None) or ""),
                        source=src,
                    )
                    return
                w = w.parent()
        except Exception:
            pass

    def _record_skip_widgets(self):
        out = []
        for attr in ("sw_record_skip", "sw_record_skip_bar"):
            w = getattr(self, attr, None)
            if w is not None:
                out.append(w)
        return out

    def _record_skip_enabled(self) -> bool:
        for w in self._record_skip_widgets():
            try:
                return bool(w.isChecked())
            except Exception:
                continue
        return True

    def _set_record_skip(self, on: bool) -> None:
        on = bool(on)
        for w in self._record_skip_widgets():
            try:
                if bool(w.isChecked()) != on:
                    w.setChecked(on)
            except Exception:
                pass
        self._refresh_record_hint()

    def _on_record_skip_toggled(self, src=None):
        if src is not None:
            try:
                on = bool(src.isChecked())
            except Exception:
                on = self._record_skip_enabled()
        else:
            on = self._record_skip_enabled()
        self._set_record_skip(on)
        self._log(
            "已下记录防重：" + ("开（命中的画质跳过）" if on else "关（不查记录，继续下载）"),
            "info",
        )
        self._schedule_prefs_save()

    def _schedule_prefs_save(self):
        w = self
        while w is not None:
            fn = getattr(w, "_schedule_save_user_prefs", None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
                return
            w = w.parent()

    def _refresh_record_hint(self, added_names=None):
        lbl = getattr(self, "lbl_record_hint", None)
        if lbl is None:
            return
        try:
            text = hint_text(
                added_names=added_names,
                skip_on=self._record_skip_enabled(),
            )
        except Exception:
            _log.exception("刷新其它视频已下记录条数失败")
            return
        if hasattr(lbl, "setFullText"):
            lbl.setFullText(text)
        else:
            lbl.setText(text)
        try:
            lbl.setToolTip(text)
        except Exception:
            pass

    def _build_record_bar(self) -> QWidget:
        bar = QWidget(self)
        bar.setObjectName("OtherVideoRecordBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setStyleSheet("#OtherVideoRecordBar{background:transparent;border:none;}")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(CARD_LEFT_GAP, 2, CARD_RIGHT_GAP, 2)
        lay.setSpacing(8)

        from pages.page_douyin import _ElideLabel
        hint = _ElideLabel("已有 0条")
        hint.setFixedHeight(MEDIUM_BUTTON_H)
        hint.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hint.setObjectName("OtherVideoRecordHint")
        hint.setStyleSheet(
            "QLabel#OtherVideoRecordHint{font-size:12px;color:#94a3b8;padding:0 4px;"
            "background:transparent;border:none;}"
        )
        self.lbl_record_hint = hint
        lay.addWidget(hint, 1)

        sw_bar = None
        try:
            from ui_main import SideToggleSwitch
            sw_bar = SideToggleSwitch(on_color=_RECORD_SKIP_YELLOW, parent=bar)
        except Exception:
            _log.exception("创建其它视频防重开关失败")
        if sw_bar is not None:
            sw_bar.setChecked(True)
            sw_bar.setToolTip(_RECORD_SKIP_TIP)
            sw_bar.clicked.connect(lambda: self._on_record_skip_toggled(sw_bar))
            self.sw_record_skip_bar = sw_bar
            lay.addWidget(sw_bar, 0, Qt.AlignVCenter)
        else:
            self.sw_record_skip_bar = None

        btn = apply_mini_button(QPushButton("已下记录"))
        btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        btn.setMinimumWidth(max(72, btn.sizeHint().width()))
        btn.setToolTip("按即梦、LibTV、XVideos、Pornhub 查看和编辑已下载记录")
        btn.clicked.connect(self._open_records_dialog)
        self.btn_records = btn
        lay.addWidget(btn, 0, Qt.AlignVCenter)
        self._record_bar = bar
        return bar

    def _record_brief(self, job) -> str:
        rec = job.get("record") or {}
        plat = rec.get("platform") or ""
        kind = rec.get("kind") or ""
        qual = rec.get("quality") or ""
        name = rec.get("name") or job.get("filename") or ""
        head = " · ".join(x for x in (plat, kind, qual) if x)
        if head and name:
            return f"{head}（{name}）"
        return name or head or "这一档"

    def _partition_recorded(self, jobs):
        """开关开着时，把已在记录里的画质拆出去。返回 (要下的, 跳过的)。"""
        try:
            known = active_names()
        except Exception:
            _log.exception("读取其它视频已下记录失败")
            known = set()
        self._record_known = known
        if not self._record_skip_enabled():
            return list(jobs), []
        kept, skipped = [], []
        for job in jobs:
            name = ((job.get("record") or {}).get("name") or "").strip()
            if name and name_aliases(name) & known:
                skipped.append(job)
            else:
                kept.append(job)
        return kept, skipped

    def _mark_already_downloaded(self, skipped):
        """勾选的每一档都已在记录里：整条按「已下载过」收口。"""
        briefs = [self._record_brief(j) for j in (skipped or [])]
        self._log("━" * 36, "info")
        self._log("📋 已下载记录：%s" % "、".join(briefs), "info")
        self._log("这些画质已在下载记录中，跳过重复下载", "ok")
        self._log("━" * 36, "info")
        self._set_status("已跳过 · 下载记录中已有", "#22c55e")
        self._last_download_ok = True
        self._last_batch_note = "已下载过"
        self._auto_dl = False
        if not set_batch_progress_note("已下载过"):
            show_cursor_toast("下载", "已下载过", accent="ok")
        self._reset_btns()
        self._report_batch_item_done()

    def _commit_saved_records(self) -> list:
        worker = getattr(self, "_dl_worker", None)
        recs = list(getattr(worker, "_saved_records", []) or []) if worker is not None else []
        if worker is not None:
            worker._saved_records = []
        if not recs:
            return []
        try:
            added = add_records(recs)
        except Exception:
            _log.exception("写入其它视频已下记录失败")
            return []
        if added:
            self._log("防重记录已更新 %d 条" % len(added), "ok")
            self._refresh_record_hint(added)
        return added

    def _on_dl_done(self, save_dir, success_count=0):
        self._commit_saved_records()
        super()._on_dl_done(save_dir, success_count)

    def _on_dl_cancel(self):
        self._commit_saved_records()
        super()._on_dl_cancel()

    def _scan_save_dir(self, folder: str, *, log_result: bool = False) -> list:
        try:
            added = scan_dir(folder)
        except Exception:
            _log.exception("扫描其它视频保存目录失败")
            added = []
        suspects = list(getattr(scan_dir, "last_suspects", []) or [])
        self._refresh_record_hint(added if added else None)
        if log_result:
            if added or suspects:
                bits = []
                if added:
                    bits.append("新增 %d 条" % len(added))
                if suspects:
                    bits.append("待核 %d 条" % len(suspects))
                self._log("已下载：" + "，".join(bits), "ok")
            else:
                self._log("已下载：未发现新记录", "ok")
        return added

    def _open_records_dialog(self):
        _open_other_video_records_dialog(self)

    def export_settings(self) -> dict:
        data = super().export_settings()
        data["check_records"] = self._record_skip_enabled()
        return data

    def apply_settings(self, d: dict):
        super().apply_settings(d if isinstance(d, dict) else {})
        if isinstance(d, dict) and "check_records" in d:
            self._set_record_skip(bool(d.get("check_records")))
        path = ""
        try:
            path = (self.save_edit.text() or "").strip()
        except Exception:
            path = ""
        if path:
            self._scan_save_dir(path, log_result=False)


def _open_other_video_records_dialog(page):
    """已下记录弹窗。正文按 # 即梦 / # LibTV / # XVideos / # Pornhub 分段。"""
    dlg = QDialog(page)
    dlg.setWindowTitle("已下记录")
    try:
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
    except Exception:
        pass
    dlg.resize(780, 440)
    dlg.setMinimumSize(620, 320)
    try:
        if page is not None and page.styleSheet():
            dlg.setStyleSheet(page.styleSheet())
    except Exception:
        pass

    v = QVBoxLayout(dlg)
    v.setContentsMargins(12, 12, 12, 12)
    v.setSpacing(10)

    preview = QTextEdit()
    preview.setAcceptRichText(False)
    preview.setPlaceholderText(_RECORD_PLACEHOLDER)
    preview.setStyleSheet(
        f"QTextEdit{{background:{tk('input_bg')};color:{tk('text')};"
        f"border:1px solid {tk('border')};border-radius:6px;font-size:13px;}}"
    )
    preview.setPlainText(format_entries(load_entries()))
    v.addWidget(preview, 1)

    _rec_file = _gallery_records_path("other_video")
    lbl_total = RecordsTotalBar(_rec_file, dlg)
    v.addWidget(lbl_total)

    def _editor_entries():
        return parse_entries(preview.toPlainText())

    def _set_total_text(extra: str = ""):
        rows = _editor_entries()
        n = sum(1 for e in rows if not e.get("suspect"))
        suspects = sum(1 for e in rows if e.get("suspect"))
        text = f"共 {n} 条"
        if suspects:
            text += f" · 待核 {suspects}"
        counts = {}
        for e in rows:
            if e.get("suspect"):
                continue
            plat = e.get("platform") or ""
            if plat in PLAT_ORDER:
                counts[plat] = counts.get(plat, 0) + 1
        bits = [f"{plat} {counts[plat]}" for plat in PLAT_ORDER if counts.get(plat)]
        if bits:
            text += " · " + " · ".join(bits)
        lbl_total.set_count_text(text + (extra or ""))

    preview.textChanged.connect(lambda: _set_total_text())
    _set_total_text()

    def _reload():
        preview.blockSignals(True)
        try:
            preview.setPlainText(format_entries(load_entries()))
        finally:
            preview.blockSignals(False)
        _set_total_text()

    def _make_btn(text, bg=None, fg=None, bd=None, hover_bg=None):
        b = QPushButton(text)
        b.setFixedHeight(34)
        b.setCursor(Qt.PointingHandCursor)
        if bg:
            qss = (
                f"QPushButton{{background:{bg};color:{fg or '#ffffff'};"
                f"border:1px solid {bd or bg};border-radius:6px;font-size:13px;}}"
            )
            if hover_bg:
                qss += f"QPushButton:hover{{background:{hover_bg};}}"
        else:
            qss = (
                f"QPushButton{{background:{tk('input_bg')};color:{tk('text')};"
                f"border:1px solid {tk('border')};border-radius:6px;font-size:13px;}}"
            )
            if hover_bg:
                qss += f"QPushButton:hover{{background:{hover_bg};}}"
        b.setStyleSheet(qss)
        return b

    def _write_editor():
        rows = _editor_entries()
        if not save_entries(rows):
            message_box_warn(dlg, "保存失败", "无法写入已下记录文件。")
            return False
        if hasattr(page, "_refresh_record_hint"):
            page._refresh_record_hint()
        return True

    def _do_add_dir():
        start = ""
        try:
            start = (page.save_edit.text() or "").strip()
        except Exception:
            start = ""
        if not start:
            start = os.path.expanduser("~/Downloads")
        folder = QFileDialog.getExistingDirectory(dlg, "选择要扫描的文件夹", start)
        if not folder:
            return
        if hasattr(page, "_scan_save_dir"):
            page._scan_save_dir(folder, log_result=True)
        _reload()

    def _do_add_file():
        def _tell(text, *, warn=False):
            fn = message_box_warn if warn else message_box_info
            QTimer.singleShot(0, lambda t=text, f=fn: f(dlg, "添加记录文件", t))

        try:
            start = os.path.join(os.path.expanduser("~"), "Desktop")
            if not os.path.isdir(start):
                start = os.path.expanduser("~")
            path, _ = QFileDialog.getOpenFileName(
                dlg, "选择记录文件", start,
                "文本文件 (*.txt);;所有文件 (*.*)",
            )
            path = (path or "").strip().strip('"')
            if not path:
                return
            incoming = read_external(path)
            if incoming is None:
                _tell(f"无法读取该文件：\n{path}", warn=True)
                return
            if not incoming:
                _tell("文件里没有可用记录（需要每行一条文件名，或按 # 即梦 / # LibTV / # XVideos / # Pornhub 分段）。", warn=True)
                return
            current = _editor_entries()
            merged, added, _changed = merge_external(current, incoming)
            preview.blockSignals(True)
            try:
                preview.setPlainText(format_entries(merged))
            finally:
                preview.blockSignals(False)
            if not save_entries(merged):
                _tell("已读入编辑框，但写入记录文件失败。", warn=True)
                _set_total_text()
                return
            if hasattr(page, "_refresh_record_hint"):
                page._refresh_record_hint(added)
            try:
                if added:
                    page._log("已下载：从文件新增 %d 条记录" % len(added), "ok")
                else:
                    page._log("已下载：文件中无新记录", "ok")
            except Exception:
                pass
            skipped = max(0, len(incoming) - len(added))
            _set_total_text(f" · 本次新增 {len(added)} 条，跳过 {skipped} 条已有")
            _tell(f"已读 {len(incoming)} 条\n新增 {len(added)} 条\n跳过 {skipped} 条（已有）")
        except Exception as e:
            _log.exception("添加其它视频记录文件失败")
            _tell(f"导入出错：{e}", warn=True)

    def _scroll_to_sep():
        text = preview.toPlainText()
        idx = text.find("-----")
        if idx < 0:
            return
        cur = preview.textCursor()
        cur.setPosition(idx)
        preview.setTextCursor(cur)
        preview.ensureCursorVisible()

    def _do_organize():
        rows = _editor_entries()
        preview.blockSignals(True)
        try:
            preview.setPlainText(format_entries(rows))
        finally:
            preview.blockSignals(False)
        if not save_entries(_editor_entries()):
            message_box_warn(dlg, "保存失败", "无法写回记录文件。")
            _set_total_text()
            return
        if hasattr(page, "_refresh_record_hint"):
            page._refresh_record_hint()
        _set_total_text()
        if any(e.get("suspect") for e in rows):
            QTimer.singleShot(0, _scroll_to_sep)

    def _do_save_close():
        if _write_editor():
            dlg.accept()

    btn_row = QHBoxLayout()
    btn_row.setSpacing(8)
    btn_add = _make_btn(
        "添加已经下目录",
        bg="#1d4ed8", fg="#dbeafe", bd="#2563eb", hover_bg="#2563eb",
    )
    btn_add.setToolTip(
        "扫描文件夹里的视频。只记即梦、LibTV、XVideos、Pornhub。"
        "新文件按 [桌面助手][平台] 归类，旧的「_原片 / _成片」按文件名末尾归类"
    )
    btn_add.clicked.connect(_do_add_dir)
    btn_row.addWidget(btn_add)

    btn_add_file = _make_btn(
        "添加记录文件",
        bg="#1d4ed8", fg="#dbeafe", bd="#2563eb", hover_bg="#2563eb",
    )
    btn_add_file.setToolTip("从其它 .txt 合并记录。可按 # 平台名 分段，也可以一行一个文件名")
    btn_add_file.clicked.connect(_do_add_file)
    btn_row.addWidget(btn_add_file)

    btn_org = _make_btn(
        "整理记录",
        bg="#a16207", fg="#fef9c3", bd="#ca8a04", hover_bg="#ca8a04",
    )
    btn_org.setToolTip(
        "按即梦 / LibTV / XVideos / Pornhub 重新分组，每组再按类型和画质分开，"
        "并把 _2、(2) 另存名挪到分隔线下面（不删除）"
    )
    btn_org.clicked.connect(_do_organize)
    btn_row.addWidget(btn_org)

    btn_row.addStretch(1)

    btn_save = _make_btn(
        "保存并关闭",
        bg="#14532d", fg="#bbf7d0", bd="#166534", hover_bg="#166534",
    )
    btn_save.clicked.connect(_do_save_close)
    btn_row.addWidget(btn_save)

    btn_close = _make_btn("关闭")
    btn_close.clicked.connect(dlg.reject)
    btn_row.addWidget(btn_close)
    v.addLayout(btn_row)

    dlg.exec_()
