# -*- coding: utf-8 -*-
"""把各段合成音频的语速拉向目标字/秒，变时长但尽量保音调。"""
from __future__ import annotations

import os
import shutil
import tempfile

TARGET_CPS = 4.3
STRETCH_MAX = 1.20
_NEAR = 0.02


def spoken_chars(text: str) -> int:
    n = len("".join(ch for ch in (text or "") if not ch.isspace()))
    return n if n > 0 else len(text or "")


def even_rate(chars, wav_s, speed=1.0, target_cps=None, max_ratio=STRETCH_MAX):
    """>1 加快，<1 放慢。已接近目标则返回 1.0。"""
    try:
        wav_s = float(wav_s or 0.0)
        chars = int(chars or 0)
        speed = float(speed or 1.0)
    except (TypeError, ValueError):
        return 1.0
    if wav_s <= 0.0 or chars <= 0:
        return 1.0
    speed = max(0.5, min(2.0, speed))
    max_ratio = max(1.01, float(max_ratio or STRETCH_MAX))
    target = float(target_cps if target_cps is not None else TARGET_CPS) * speed
    if target <= 0:
        return 1.0
    actual = chars / wav_s
    if actual <= 0:
        return 1.0
    rate = target / actual
    lo, hi = 1.0 / max_ratio, max_ratio
    if rate < lo:
        rate = lo
    elif rate > hi:
        rate = hi
    if abs(rate - 1.0) <= _NEAR:
        return 1.0
    return float(rate)


def _ffmpeg_bin():
    found = shutil.which("ffmpeg")
    if found:
        return found
    for path in (r"C:\ffmpeg\bin\ffmpeg.exe", r"C:\ffmpeg\bin\ffmpeg.EXE"):
        if os.path.isfile(path):
            return path
    return ""


def even_samples(samples, sr, rate):
    """Pitch-preserving time stretch of a mono/stereo float array."""
    import numpy as np

    rate = float(rate)
    if abs(rate - 1.0) <= _NEAR or samples is None:
        return samples
    arr = np.asarray(samples)
    if arr.size == 0:
        return samples
    try:
        import librosa

        if arr.ndim == 1:
            return librosa.effects.time_stretch(arr.astype(np.float32), rate=rate)
        out = []
        axis = 0 if arr.shape[0] <= 8 else 1
        nch = arr.shape[axis]
        for i in range(nch):
            ch = arr[i] if axis == 0 else arr[:, i]
            out.append(librosa.effects.time_stretch(ch.astype(np.float32), rate=rate))
        return np.stack(out, axis=axis)
    except Exception:
        return samples


def even_out_wav(path, text, speed=1.0, target_cps=None, max_ratio=STRETCH_MAX):
    """就地把 wav 拉向目标字/秒。返回实际使用的倍率（1.0 表示未改）。"""
    if not path or not os.path.isfile(path):
        return 1.0
    try:
        import wave

        with wave.open(path, "rb") as w:
            frames = int(w.getnframes() or 0)
            sr = int(w.getframerate() or 0)
        wav_s = (float(frames) / float(sr)) if sr > 0 else 0.0
    except Exception:
        return 1.0
    rate = even_rate(spoken_chars(text), wav_s, speed=speed, target_cps=target_cps, max_ratio=max_ratio)
    if rate == 1.0:
        return 1.0
    if _stretch_ffmpeg(path, rate):
        return rate
    if _stretch_librosa(path, rate):
        return rate
    return 1.0


def _stretch_ffmpeg(path, rate):
    import subprocess

    bin_path = _ffmpeg_bin()
    if not bin_path:
        return False
    fd, tmp = tempfile.mkstemp(prefix="even-", suffix=".wav", dir=os.path.dirname(path) or None)
    os.close(fd)
    try:
        cmd = [
            bin_path, "-y", "-hide_banner", "-loglevel", "error",
            "-i", path, "-filter:a", "atempo=%.5f" % float(rate),
            tmp,
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=30)
        if r.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) < 64:
            return False
        os.replace(tmp, path)
        return True
    except Exception:
        return False
    finally:
        if os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _stretch_librosa(path, rate):
    try:
        import soundfile as sf
    except Exception:
        return False
    try:
        data, sr = sf.read(path, always_2d=False)
        out = even_samples(data, sr, rate)
        if out is None:
            return False
        sf.write(path, out, sr)
        return True
    except Exception:
        return False