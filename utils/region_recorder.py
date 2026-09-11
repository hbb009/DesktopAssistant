# utils/region_recorder.py
# ---------------------------------------------------------------------------
# 区域录屏：检测 ffmpeg、用 gdigrab 录 Windows 桌面矩形区域、优雅停止。
# ---------------------------------------------------------------------------

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from utils.logger import get_logger

log = get_logger(__name__)

REGION_RECORD_SUBDIR = "RegionRecord"
MAX_SECONDS = 60
DEFAULT_FPS = 30
# 画质：固定「尽量最好」（录屏最长 60s，体积可接受）
# CRF 12 ≈ 观感近无损；preset slow + yuv444p 利于 UI/小字边缘
DEFAULT_CRF = 12
DEFAULT_PRESET = "slow"

# Windows 子进程不弹黑窗
_CREATE_NO_WINDOW = 0x08000000 if sys.platform.startswith("win") else 0


def find_ffmpeg() -> Optional[str]:
    """返回 ffmpeg 可执行路径；找不到则 None。"""
    path = shutil.which("ffmpeg")
    if path:
        return path
    # 常见 scoop / chocolatey / gyan 安装路径兜底
    candidates = []
    if sys.platform.startswith("win"):
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")
        candidates = [
            os.path.join(pf, "ffmpeg", "bin", "ffmpeg.exe"),
            os.path.join(pf86, "ffmpeg", "bin", "ffmpeg.exe"),
            os.path.join(local, "Microsoft", "WinGet", "Links", "ffmpeg.exe"),
        ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def find_ffprobe() -> Optional[str]:
    """与 ffmpeg 同目录的 ffprobe；没有则 which。"""
    path = shutil.which("ffprobe")
    if path:
        return path
    ff = find_ffmpeg()
    if ff:
        base = os.path.dirname(ff)
        name = "ffprobe.exe" if sys.platform.startswith("win") else "ffprobe"
        cand = os.path.join(base, name)
        if os.path.isfile(cand):
            return cand
    return None


def probe_video_info(path: str) -> Tuple[int, int, float]:
    """读取视频宽、高、时长（秒）。失败返回 (0, 0, 0.0)。"""
    path = (path or "").strip()
    if not path or not os.path.isfile(path):
        return 0, 0, 0.0

    flags = _CREATE_NO_WINDOW
    probe = find_ffprobe()
    if probe:
        try:
            # 分两次读更稳：stream 宽高 + format 时长
            cmd_wh = [
                probe, "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0:s=x",
                path,
            ]
            r1 = subprocess.run(
                cmd_wh, capture_output=True, text=True, timeout=12,
                creationflags=flags,
            )
            wh = (r1.stdout or "").strip().splitlines()
            w = h = 0
            if wh:
                # "1920x1080"
                part = wh[0].strip().lower().replace(",", "x")
                if "x" in part:
                    a, b = part.split("x", 1)
                    w, h = int(float(a)), int(float(b))
            cmd_d = [
                probe, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                path,
            ]
            r2 = subprocess.run(
                cmd_d, capture_output=True, text=True, timeout=12,
                creationflags=flags,
            )
            dur = 0.0
            ds = (r2.stdout or "").strip().splitlines()
            if ds:
                try:
                    dur = float(ds[0].strip())
                except ValueError:
                    dur = 0.0
            if w > 0 and h > 0:
                return w, h, max(0.0, dur)
        except Exception:
            log.debug("ffprobe meta failed: %s", path, exc_info=True)

    # 兜底：ffmpeg -i 解析 stderr
    ff = find_ffmpeg()
    if not ff:
        return 0, 0, 0.0
    try:
        import re
        r = subprocess.run(
            [ff, "-i", path],
            capture_output=True, text=True, timeout=12,
            creationflags=flags,
        )
        err = r.stderr or ""
        w = h = 0
        dur = 0.0
        m = re.search(r"(\d{2,5})x(\d{2,5})", err)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
        m2 = re.search(
            r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err
        )
        if m2:
            dur = (
                int(m2.group(1)) * 3600
                + int(m2.group(2)) * 60
                + float(m2.group(3))
            )
        return w, h, max(0.0, dur)
    except Exception:
        log.debug("ffmpeg -i meta failed: %s", path, exc_info=True)
        return 0, 0, 0.0


def format_duration(sec: float) -> str:
    """时长展示：12.3s / 1:05 / 1:02:03。"""
    try:
        s = float(sec)
    except (TypeError, ValueError):
        return "—"
    if s <= 0:
        return "—"
    if s < 60:
        # 录屏多为短片，保留一位小数更直观
        if s < 10:
            return f"{s:.1f}s"
        return f"{int(round(s))}s"
    total = int(round(s))
    h, rem = divmod(total, 3600)
    m, sec_i = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{sec_i:02d}"
    return f"{m}:{sec_i:02d}"


def ffmpeg_missing_message() -> str:
    return (
        "未检测到 ffmpeg，无法区域录屏。\n\n"
        "请安装后确保在 PATH 中，例如：\n"
        "  winget install Gyan.FFmpeg\n"
        "或 scoop install ffmpeg\n\n"
        "安装后重启本程序。"
    )


def ensure_even(n: int) -> int:
    n = max(2, int(n))
    return n - (n % 2)


@dataclass
class RecordResult:
    ok: bool
    path: str = ""
    error: str = ""
    elapsed: float = 0.0


class RegionRecorder:
    """管理一次区域录制进程。线程安全：stop 可从 UI 线程调用。"""

    def __init__(self):
        self._proc: Optional[subprocess.Popen] = None
        self._path: str = ""
        self._started_at: float = 0.0
        self._lock = threading.Lock()
        self._stderr_buf: list = []
        self._stderr_thread: Optional[threading.Thread] = None

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    @property
    def output_path(self) -> str:
        return self._path

    def elapsed_seconds(self) -> float:
        if not self._started_at:
            return 0.0
        return max(0.0, time.monotonic() - self._started_at)

    def start(
        self,
        out_path: str,
        offset_x: int,
        offset_y: int,
        width: int,
        height: int,
        *,
        fps: int = DEFAULT_FPS,
        draw_mouse: bool = True,
        max_seconds: int = MAX_SECONDS,
    ) -> Tuple[bool, str]:
        """启动录制。成功返回 (True, "")，失败 (False, 原因)。"""
        if not sys.platform.startswith("win"):
            return False, "区域录屏目前仅支持 Windows（ffmpeg gdigrab）"

        ff = find_ffmpeg()
        if not ff:
            return False, ffmpeg_missing_message()

        if self.is_recording:
            return False, "已在录制中"

        w = ensure_even(width)
        h = ensure_even(height)
        if w < 32 or h < 32:
            return False, "选区太小（至少约 32×32）"

        out_path = os.path.normpath(out_path)
        parent = os.path.dirname(out_path)
        try:
            os.makedirs(parent, exist_ok=True)
        except Exception as e:
            return False, f"无法创建保存目录：{e}"

        # gdigrab：offset / video_size 为物理桌面像素（调用方已乘 DPR）
        # 最高实用画质（≤60s）：CRF 12 + slow + yuv444p（彩边/文字更锐）
        crf = int(DEFAULT_CRF)
        preset = str(DEFAULT_PRESET or "slow")
        cmd = [
            ff,
            "-y",
            "-f", "gdigrab",
            "-framerate", str(max(1, int(fps))),
            "-offset_x", str(int(offset_x)),
            "-offset_y", str(int(offset_y)),
            "-video_size", f"{w}x{h}",
            "-draw_mouse", "1" if draw_mouse else "0",
            "-i", "desktop",
            "-t", str(max(1, int(max_seconds))),
            "-c:v", "libx264",
            "-preset", preset,
            "-crf", str(max(0, min(28, crf))),
            # yuv444p：保留色度，UI 描边/彩字比 420 更清晰（VLC/现代播放器均可用）
            "-pix_fmt", "yuv444p",
            "-x264-params",
            "aq-mode=3:aq-strength=1.2:ref=5:me=umh:subme=9:trellis=2:psy-rd=1.0,0.15",
            "-movflags", "+faststart",
            out_path,
        ]

        log.info(
            "region_record start size=%sx%s offset=(%s,%s) -> %s",
            w, h, offset_x, offset_y, out_path,
        )

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=_CREATE_NO_WINDOW,
            )
        except Exception as e:
            log.exception("启动 ffmpeg 失败")
            return False, f"启动 ffmpeg 失败：{e}"

        with self._lock:
            self._proc = proc
            self._path = out_path
            self._started_at = time.monotonic()
            self._stderr_buf = []

        def _drain():
            try:
                if proc.stderr:
                    for line in iter(proc.stderr.readline, b""):
                        if not line:
                            break
                        try:
                            self._stderr_buf.append(line.decode("utf-8", errors="replace"))
                        except Exception:
                            pass
            except Exception:
                pass

        self._stderr_thread = threading.Thread(target=_drain, daemon=True)
        self._stderr_thread.start()
        return True, ""

    def stop(self, discard: bool = False) -> RecordResult:
        """停止录制。discard=True 时删除输出文件。"""
        with self._lock:
            proc = self._proc
            path = self._path
            started = self._started_at
            self._proc = None

        if proc is None:
            return RecordResult(ok=False, error="当前没有进行中的录制")

        elapsed = max(0.0, time.monotonic() - started) if started else 0.0

        # 优雅结束：向 stdin 写 q
        try:
            if proc.poll() is None and proc.stdin:
                try:
                    proc.stdin.write(b"q")
                    proc.stdin.flush()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.wait(timeout=2)
                    except Exception:
                        pass
        except Exception as e:
            log.exception("停止 ffmpeg 异常")
            try:
                proc.kill()
            except Exception:
                pass
            return RecordResult(ok=False, path=path, error=f"停止录制失败：{e}", elapsed=elapsed)

        if discard:
            try:
                if path and os.path.isfile(path):
                    os.remove(path)
            except Exception:
                log.exception("删除未保存录像失败 path=%s", path)
            return RecordResult(ok=True, path="", error="", elapsed=elapsed)

        # 校验输出
        if not path or not os.path.isfile(path) or os.path.getsize(path) < 256:
            tail = "".join(self._stderr_buf[-30:]) if self._stderr_buf else ""
            err = "录像文件未生成或过小"
            if tail.strip():
                err = f"{err}\n{tail.strip()[-500:]}"
            log.warning("region_record failed: %s", err)
            return RecordResult(ok=False, path=path or "", error=err, elapsed=elapsed)

        log.info("region_record saved path=%s elapsed=%.1fs", path, elapsed)
        return RecordResult(ok=True, path=path, error="", elapsed=elapsed)

    def force_kill(self):
        """关窗等紧急情况：尽量停掉子进程。"""
        try:
            self.stop(discard=False)
        except Exception:
            with self._lock:
                proc = self._proc
                self._proc = None
            if proc is not None:
                try:
                    proc.kill()
                except Exception:
                    pass
