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

from PyQt5.QtCore import QThread, pyqtSignal

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
from utils.logger import get_logger
from utils.cursor_toast import in_batch_download
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
                            self._emit(f"✓ {label} · {os.path.basename(outp)}", "ok")
                    else:
                        ok_n += 1
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


class PageOtherVideo(PageJimeng):
    """其它视频子页：一套 UI，按链接区分即梦 / LibTV / XVideos。"""

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
        for c in self._cards:
            if not c.is_checked():
                continue
            it = c.item
            jobs.append({
                "label": it.label or "视频",
                "url": it.url,
                "filename": self._make_filename(it),
                "referer": referer,
                "extract_audio": bool(getattr(it, "extract_audio", False)),
                "page_url": page_url,
                # 令牌过期重解析时要按平台走各自的解析器
                "platform": str((self._info or {}).get("platform")
                                or self._current_platform or ""),
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
        self._start_download(jobs)

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
