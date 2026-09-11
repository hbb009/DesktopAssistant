# utils/cosyvoice_clone.py
# 语音克隆：找权重 / CosyVoice 运行库，子进程探测与合成。
# 不要在模块顶层 import torch / cosyvoice（又慢又会和 Qt 抢 DLL）。

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import time
from datetime import datetime

from PyQt5.QtCore import QThread, pyqtSignal


INSTRUCT_PRESETS = [
    ("", "默认 · 零样本克隆"),
    ("请用开心的语气说。", "开心"),
    ("请用温柔的语气说。", "温柔"),
    ("请用悲伤的语气说。", "悲伤"),
    ("请用生气的语气说。", "生气"),
    ("请用广东话表达。", "广东话"),
    ("请用四川话表达。", "四川话"),
    ("请用东北话表达。", "东北话"),
    ("请用上海话表达。", "上海话"),
    ("请用尽可能快地语速说。", "加快语速"),
    ("请用尽可能慢地语速说。", "放慢语速"),
]

SPEED_CHOICES = [
    (0.8, "0.8× 稍慢"),
    (1.0, "1.0× 原速"),
    (1.2, "1.2× 稍快"),
]

AUDIO_EXTS = {
    ".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac", ".wma", ".opus", ".webm",
}
AUDIO_FILTER = (
    "音频 (*.wav *.mp3 *.flac *.m4a *.ogg *.aac *.wma *.opus *.webm);;所有文件 (*.*)"
)

# CosyVoice frontend 只支持 ≤30 秒的参考音频，超出会自动裁切。
REF_AUDIO_MAX_SEC = 30.0

_WEIGHT_MARKERS = ("llm.pt", "llm.rl.pt", "flow.pt", "hift.pt")
_YAML_MARKERS = ("cosyvoice3.yaml", "cosyvoice.yaml", "cosyvoice2.yaml")


def _pkg_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def work_dir() -> str:
    try:
        from utils.app_paths import voice_clone_dir
        return voice_clone_dir()
    except Exception:
        d = os.path.join(os.path.expanduser("~"), "Desktop Assistant", "voice_clone")
        os.makedirs(d, exist_ok=True)
        return d


def _looks_like_cosyvoice_weights(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    names = set()
    try:
        names = set(os.listdir(path))
    except Exception:
        return False
    has_llm = ("llm.pt" in names) or ("llm.rl.pt" in names)
    has_flow = "flow.pt" in names
    has_hift = "hift.pt" in names
    has_yaml = any(n in names for n in _YAML_MARKERS)
    return has_llm and has_flow and (has_hift or has_yaml)


def find_cosyvoice_home() -> str:
    """官方 CosyVoice 仓库根目录（含 cosyvoice/cli/cosyvoice.py）。"""
    cands = []
    env = (os.environ.get("COSYVOICE_HOME") or "").strip()
    if env:
        cands.append(os.path.expandvars(os.path.expanduser(env)))
    try:
        from utils.app_paths import app_root, model_dir, tools_dir
        root = app_root()
        md = model_dir()
        td = tools_dir()
        cands.extend([
            os.path.join(md, "CosyVoice"),
            os.path.join(md, "runtime", "CosyVoice"),
            os.path.join(td, "CosyVoice"),
            os.path.join(root, "CosyVoice"),
        ])
    except Exception:
        pass
    seen = set()
    for raw in cands:
        p = os.path.abspath(os.path.normpath(raw))
        key = os.path.normcase(p)
        if key in seen:
            continue
        seen.add(key)
        marker = os.path.join(p, "cosyvoice", "cli", "cosyvoice.py")
        if os.path.isfile(marker):
            return p
    return ""


def scan_model_dirs() -> list:
    """model/ 下可用的 CosyVoice 权重目录 [(path, name), ...]。"""
    try:
        from utils.app_paths import model_dir
        root = model_dir()
    except Exception:
        return []
    found = []
    try:
        names = os.listdir(root)
    except Exception:
        return []
    for name in names:
        p = os.path.join(root, name)
        if _looks_like_cosyvoice_weights(p):
            found.append((p, name))
    found.sort(key=lambda x: (0 if "CosyVoice3" in x[1] else 1, x[1].lower()))
    return found


def default_model_dir() -> str:
    items = scan_model_dirs()
    for path, name in items:
        if "Fun-CosyVoice3" in name or "CosyVoice3" in name:
            return path
    return items[0][0] if items else ""


def weights_status(model_path: str = "") -> dict:
    """只看磁盘，不 import torch。"""
    path = (model_path or default_model_dir() or "").strip()
    home = find_cosyvoice_home()
    pkg = _pkg_available("cosyvoice")
    name = os.path.basename(path) if path else ""
    return {
        "has_weights": bool(path and os.path.isdir(path) and _looks_like_cosyvoice_weights(path)),
        "model_dir": path,
        "model_name": name,
        "has_source": bool(home) or pkg,
        "cosyvoice_home": home,
        "cosyvoice_pkg": pkg,
        "torch_pkg": _pkg_available("torch"),
        "torchaudio_pkg": _pkg_available("torchaudio"),
    }


def status_summary(st: dict, runtime: dict | None = None) -> tuple:
    """(ok, short_label, detail)。"""
    st = st or {}
    name = st.get("model_name") or "Fun-CosyVoice3-0.5B"
    if not st.get("has_weights"):
        return False, "未放入模型", "请把 Fun-CosyVoice3-0.5B 放到 model/ 目录"
    if not st.get("has_source"):
        return False, "缺推理代码", (
            f"权重已经找到：{name}\n\n"
            "那是模型文件（约 9GB），不能改名当程序用。\n"
            "还差官方推理源码 CosyVoice（Python 代码，通常几十 MB，不是再下一份模型）。\n"
            "请克隆到 model/CosyVoice。"
        )
    if runtime:
        if runtime.get("ok"):
            gpu = runtime.get("gpu") or ("CUDA" if runtime.get("cuda") else "CPU")
            return True, "可用", f"{name} · {gpu}"
        err = (runtime.get("error") or "").strip()
        if "torch" in err.lower() or not st.get("torch_pkg"):
            return False, "缺 PyTorch", err or "请安装 torch / torchaudio"
        return False, "推理代码异常", err or "子进程探测失败"
    if not st.get("torch_pkg"):
        return False, "缺 PyTorch", (
            f"权重已经找到：{name}\n"
            "还要安装 PyTorch 才能本地合成。"
        )
    return False, "待检测", f"权重已找到：{name}。点「检测」确认推理代码。"


def install_hint_text() -> str:
    try:
        from utils.app_paths import model_dir
        dest = os.path.join(model_dir(), "CosyVoice")
        weights = os.path.join(model_dir(), "Fun-CosyVoice3-0.5B")
    except Exception:
        dest = r"model\CosyVoice"
        weights = r"model\Fun-CosyVoice3-0.5B"
    clone = f'git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git "{dest}"'
    torch_cmd = (
        "python -m pip install torch torchaudio "
        "--index-url https://download.pytorch.org/whl/cu124"
    )
    req = f'python -m pip install -r "{os.path.join(dest, "requirements.txt")}"'
    return (
        "两套东西不要混：\n"
        f"· 权重（约 9GB，已放好就不用再下）：{weights}\n"
        f"· 运行库（GitHub 源码，通常几十 MB，不是模型）：{dest}\n\n"
        "不要把 Fun-CosyVoice3-0.5B 改名为 CosyVoice。\n\n"
        "还缺运行库时执行：\n"
        f"  {clone}\n\n"
        "再装 PyTorch（按本机 CUDA 改 cu124）：\n"
        f"  {torch_cmd}\n\n"
        "其余依赖：\n"
        f"  {req}\n\n"
        "装好后回到本页点「刷新 / 检测」。也可设置 COSYVOICE_HOME 指向源码目录。"
    )


def clone_cmd() -> str:
    try:
        from utils.app_paths import model_dir
        dest = os.path.join(model_dir(), "CosyVoice")
    except Exception:
        dest = r"model\CosyVoice"
    return f'git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git "{dest}"'


def _job_cmd(meta_path: str) -> list:
    try:
        from utils.app_paths import is_frozen
        frozen = is_frozen()
    except Exception:
        frozen = bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")
    if frozen:
        from utils.deskassist_runtime import find_python, meipass
        py = find_python()
        worker = meipass() / "cosyvoice_worker" / "run.py"
        if py and worker.is_file():
            return [py, str(worker), meta_path]
        return [sys.executable, "--cosyvoice-job", meta_path]
    return [sys.executable, "-m", "utils.cosyvoice_job", meta_path]


def _popen_kwargs(cwd: str, env: dict) -> dict:
    import subprocess

    kw = {
        "cwd": cwd,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if sys.platform == "win32":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            kw["startupinfo"] = si
        except Exception:
            pass
    return kw


def _app_cwd() -> str:
    try:
        from utils.app_paths import app_root
        return app_root()
    except Exception:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_job(meta: dict, timeout: int, cancel_flag=None) -> dict:
    import subprocess
    import tempfile

    work = work_dir()
    os.makedirs(work, exist_ok=True)
    result_path = meta.get("result") or ""
    with tempfile.TemporaryDirectory(prefix="deskassist_cosy_", dir=work) as td:
        meta_path = os.path.join(td, "meta.json")
        if not result_path:
            result_path = os.path.join(td, "result.json")
            meta = dict(meta)
            meta["result"] = result_path
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        env = os.environ.copy()
        env.setdefault("OMP_NUM_THREADS", "1")
        env.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
        env.pop("QT_PLUGIN_PATH", None)
        env.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)
        try:
            from utils.deskassist_runtime import meipass
            env["COSYVOICE_MEIPASS"] = str(meipass())
            env["OCR_MEIPASS"] = env["COSYVOICE_MEIPASS"]
            env["PYTHONPATH"] = str(meipass()) + os.pathsep + env.get("PYTHONPATH", "")
        except Exception:
            pass
        home = (meta.get("cosyvoice_home") or "").strip()
        if home:
            env["COSYVOICE_HOME"] = home
            env["PYTHONPATH"] = home + os.pathsep + env.get("PYTHONPATH", "")

        cmd = _job_cmd(meta_path)
        kw = _popen_kwargs(_app_cwd(), env)
        proc = subprocess.Popen(cmd, **kw)
        if isinstance(cancel_flag, list):
            cancel_flag.append(proc)
        try:
            stdout, stderr = proc.communicate(timeout=int(timeout))
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
            stdout, stderr = proc.communicate(timeout=8)
            raise RuntimeError(f"子进程超时（{timeout}s）")
        code = proc.returncode
        data = {}
        if os.path.isfile(result_path):
            try:
                with open(result_path, encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:
                data = {}
        if data.get("ok"):
            return data
        detail = (data.get("error") or "").strip()
        if not detail:
            detail = ((stderr or "") + "\n" + (stdout or "")).strip()
        if int(code or 0) in (-1073741819, 3221225477):
            detail = (
                (detail + "\n") if detail else ""
            ) + "子进程 access violation（请确认 cosyvoice_job 未 import PyQt5）"
        raise RuntimeError(
            detail[-2000:] if detail else f"子进程失败 code={code}"
        )


def save_wav_float32(path: str, audio, samplerate: int = 16000) -> str:
    """把 float32 单声道数组写成 16-bit PCM wav。"""
    import numpy as np
    import wave

    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    arr = np.asarray(audio, dtype="float32").reshape(-1)
    arr = np.clip(arr, -1.0, 1.0)
    pcm = (arr * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(samplerate or 16000))
        w.writeframes(pcm.tobytes())
    return path


def _pcm_bytes_to_float32(raw, sampwidth: int, channels: int):
    """整数 PCM 字节 → float32 单声道。"""
    import numpy as np

    if sampwidth == 2:
        audio = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
    elif sampwidth == 4:
        audio = np.frombuffer(raw, dtype="<i4").astype("float32") / 2147483648.0
    elif sampwidth == 1:
        audio = (np.frombuffer(raw, dtype="u1").astype("float32") - 128.0) / 128.0
    elif sampwidth == 3:
        a = np.frombuffer(raw, dtype="u1")
        n = (len(a) // 3) * 3
        a = a[:n].reshape(-1, 3)
        vals = (
            a[:, 0].astype("int32")
            | (a[:, 1].astype("int32") << 8)
            | (a[:, 2].astype("int32") << 16)
        )
        vals = np.where(vals & 0x800000, vals - 0x1000000, vals)
        audio = vals.astype("float32") / 8388608.0
    else:
        raise ValueError(f"unsupported PCM sampwidth={sampwidth}")
    ch = max(1, int(channels or 1))
    if ch > 1:
        usable = (len(audio) // ch) * ch
        audio = audio[:usable].reshape(-1, ch).mean(axis=1)
    return audio


def _load_wav_via_wave(path: str):
    """标准库 wave：只认整数 PCM（format 1）。"""
    import wave

    with wave.open(path, "rb") as w:
        sr = int(w.getframerate() or 0)
        ch = int(w.getnchannels() or 1)
        sw = int(w.getsampwidth() or 2)
        n = int(w.getnframes() or 0)
        raw = w.readframes(n)
    return _pcm_bytes_to_float32(raw, sw, ch), sr


def _load_wav_riff(path: str):
    """手读 RIFF：补 wave 不支持的 IEEE float（format 3）和 WAVE_FORMAT_EXTENSIBLE。"""
    import struct
    import numpy as np

    with open(path, "rb") as f:
        blob = f.read()
    if len(blob) < 12 or blob[0:4] != b"RIFF" or blob[8:12] != b"WAVE":
        raise ValueError("not a RIFF WAVE")
    pos = 12
    fmt = None
    payload = None
    while pos + 8 <= len(blob):
        cid = blob[pos:pos + 4]
        csz = struct.unpack_from("<I", blob, pos + 4)[0]
        start = pos + 8
        end = min(len(blob), start + csz)
        if cid == b"fmt ":
            fmt = blob[start:end]
        elif cid == b"data":
            payload = blob[start:end]
            break
        pos = end + (csz & 1)
    if fmt is None or payload is None or len(fmt) < 16:
        raise ValueError("missing fmt/data chunk")
    tag, ch, sr, _avg, _align, bits = struct.unpack_from("<HHIIHH", fmt, 0)
    # WAVE_FORMAT_EXTENSIBLE：真正的编码在 SubFormat GUID 前 2 字节
    if tag == 0xFFFE and len(fmt) >= 40:
        tag = struct.unpack_from("<H", fmt, 24)[0]
    ch = max(1, int(ch or 1))
    sr = int(sr or 0)
    if tag == 3:
        if bits == 32:
            audio = np.frombuffer(payload, dtype="<f4").copy()
        elif bits == 64:
            audio = np.frombuffer(payload, dtype="<f8").astype("float32")
        else:
            raise ValueError(f"unsupported IEEE float bits={bits}")
        if ch > 1:
            usable = (len(audio) // ch) * ch
            audio = audio[:usable].reshape(-1, ch).mean(axis=1)
        return audio.astype("float32", copy=False), sr
    if tag == 1:
        sw = max(1, int(bits or 16) // 8)
        return _pcm_bytes_to_float32(payload, sw, ch), sr
    raise ValueError(f"unknown wav format tag={tag}")


def load_wav_float32(path: str):
    """读取 wav → (float32 单声道, sr)。读不了返回 (None, 0)，不抛异常。

    CosyVoice / torchaudio 默认写出 IEEE float wav（format 3），
    标准库 wave 会报 unknown format: 3。
    """
    if not path or not os.path.isfile(path):
        return None, 0
    if os.path.splitext(path)[1].lower() != ".wav":
        return None, 0
    try:
        audio, sr = _load_wav_via_wave(path)
        if audio is not None and sr > 0:
            return audio, sr
    except Exception:
        pass
    try:
        audio, sr = _load_wav_riff(path)
        if audio is not None and sr > 0:
            return audio, sr
    except Exception:
        pass
    return None, 0


def new_ref_wav_path() -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(work_dir(), f"ref_{ts}.wav")


_HANZI_RE = re.compile(r"[\u4e00-\u9fff]")


def _first_hanzi(text: str, n: int, fallback: str) -> str:
    """取前 n 个汉字；没有汉字时退回字母数字，再不行用 fallback。"""
    n = max(0, int(n or 0))
    chars = _HANZI_RE.findall(text or "")
    if chars:
        return "".join(chars[:n])
    safe = re.sub(r"[^0-9A-Za-z]+", "", text or "")
    if safe:
        return safe[:n]
    return fallback


def _next_wav_version(folder: str, prefix: str) -> int:
    """已有 {prefix}_{NN}.wav 的下一号，从 1 起。"""
    max_n = 0
    head = prefix + "_"
    try:
        names = os.listdir(folder)
    except OSError:
        return 1
    for name in names:
        base, ext = os.path.splitext(name)
        if ext.lower() != ".wav" or not base.startswith(head):
            continue
        tail = base[len(head):]
        if tail.isdigit():
            try:
                max_n = max(max_n, int(tail))
            except (TypeError, ValueError):
                pass
    return max_n + 1


def new_out_wav_path(
    save_dir: str = "",
    voice_name: str = "",
    tts_text: str = "",
) -> str:
    """原声名前5字_字幕前8字_YYYYMMDD_版本号.wav"""
    folder = (save_dir or "").strip() or work_dir()
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception:
        folder = work_dir()
    voice = _first_hanzi(voice_name, 5, "原声")
    caption = _first_hanzi(tts_text, 8, "字幕")
    date = datetime.now().strftime("%Y%m%d")
    prefix = f"{voice}_{caption}_{date}"
    ver = _next_wav_version(folder, prefix)
    if ver < 1:
        ver = 1
    path = os.path.join(folder, f"{prefix}_{ver:02d}.wav")
    while os.path.exists(path):
        ver += 1
        path = os.path.join(folder, f"{prefix}_{ver:02d}.wav")
    return path


def wav_duration_sec(path: str) -> float:
    try:
        audio, sr = load_wav_float32(path)
    except Exception:
        return 0.0
    if audio is None or sr <= 0:
        return 0.0
    return float(len(audio)) / float(sr)


def is_audio_file(path: str) -> bool:
    if not path or not os.path.isfile(path):
        return False
    return os.path.splitext(path)[1].lower() in AUDIO_EXTS


def first_audio_from_urls(urls) -> str:
    """从 QUrl 列表里取出第一个本地音频文件。"""
    for url in urls or []:
        try:
            p = url.toLocalFile() if hasattr(url, "toLocalFile") else str(url)
        except Exception:
            p = ""
        p = os.path.abspath(p) if p else ""
        if is_audio_file(p):
            return p
    return ""


def import_audio_file(src: str) -> str:
    """把用户上传的声音拷进工作目录；能转 wav 就转（ffmpeg）。"""
    import shutil

    src = os.path.abspath(src or "")
    if not os.path.isfile(src):
        raise FileNotFoundError("找不到音频文件")
    ext = os.path.splitext(src)[1].lower()
    if ext not in AUDIO_EXTS:
        raise ValueError(f"不支持的音频格式：{ext or '无扩展名'}")

    work = work_dir()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest = os.path.join(work, f"upload_{ts}{ext}")
    if os.path.normcase(os.path.abspath(src)) != os.path.normcase(os.path.abspath(dest)):
        shutil.copy2(src, dest)
    else:
        dest = src
    wav = _ffmpeg_to_wav(dest)
    if wav and os.path.isfile(wav):
        if wav != dest and ext != ".wav":
            try:
                os.remove(dest)
            except Exception:
                pass
        return wav
    return dest


def _crop_wav(src: str, max_sec: float) -> str:
    """把 wav 裁到 max_sec 秒（保持 16k 单声道），另存 crop_<ts>.wav。失败返回空串。"""
    try:
        from utils.region_recorder import find_ffmpeg
        ff = find_ffmpeg()
    except Exception:
        ff = None
    if not ff:
        return ""
    import subprocess

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(os.path.dirname(src) or ".", f"crop_{ts}.wav")
    cmd = [ff, "-y", "-i", src, "-t", f"{max_sec:.3f}", "-c:a", "pcm_s16le", "-vn", out]
    kw = {"capture_output": True, "timeout": 120}
    if sys.platform == "win32":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        r = subprocess.run(cmd, **kw)
    except Exception:
        return ""
    if r.returncode == 0 and os.path.isfile(out) and os.path.getsize(out) > 64:
        return out
    return ""


def import_reference_audio(
    src: str, max_sec: float = REF_AUDIO_MAX_SEC
) -> tuple:
    """导入参考音频，返回 (wav路径, 是否已自动裁剪)。

    超过 max_sec 秒会自动裁掉尾部并另存 crop_*.wav（CosyVoice 只支持
    ≤30s 参考音频）。不弹窗，调用方用第二个返回值更新提示即可。
    """
    dest = import_audio_file(src)
    if not dest or not os.path.isfile(dest):
        return dest, False
    try:
        max_sec = float(max_sec or 0.0)
    except (TypeError, ValueError):
        max_sec = 0.0
    if max_sec <= 0:
        return dest, False
    dur = wav_duration_sec(dest)
    if not (dur and dur > max_sec):
        return dest, False
    crop = _crop_wav(dest, max_sec)
    if crop and os.path.isfile(crop):
        return crop, True
    return dest, False


def _wav_is_pcm(path: str) -> bool:
    """标准库 wave 能打开 = 整数 PCM，IEEE float 会失败。"""
    try:
        import wave
        with wave.open(path, "rb") as w:
            return int(w.getnframes() or 0) > 0 and int(w.getframerate() or 0) > 0
    except Exception:
        return False


def _ffmpeg_to_wav(src: str) -> str:
    """尽量转成 16k 单声道 16-bit PCM wav，失败则返回空串。"""
    if not src or not os.path.isfile(src):
        return ""
    ext = os.path.splitext(src)[1].lower()
    if ext == ".wav" and _wav_is_pcm(src):
        return src
    try:
        from utils.region_recorder import find_ffmpeg
        ff = find_ffmpeg()
    except Exception:
        ff = None
    if not ff:
        return ""
    import subprocess

    base = os.path.splitext(src)[0]
    # 源已经是 .wav 时不能原地覆盖（ffmpeg 读自己写自己会坏）
    out = base + ".wav" if ext != ".wav" else base + "_pcm.wav"
    cmd = [
        ff, "-y", "-i", src,
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-vn", out,
    ]
    kw = {
        "capture_output": True,
        "timeout": 120,
    }
    if sys.platform == "win32":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        r = subprocess.run(cmd, **kw)
    except Exception:
        return ""
    if r.returncode == 0 and os.path.isfile(out) and os.path.getsize(out) > 64:
        return out
    return ""


def _clean_asr_text(text: str) -> str:
    """Whisper 稿子：压空白，去掉汉字之间的空格。"""
    t = (text or "").replace("\r", " ").replace("\n", " ").strip()
    if not t:
        return ""
    parts = t.split()
    out = [parts[0]]
    for p in parts[1:]:
        prev = out[-1]
        if (
            prev
            and p
            and ("\u4e00" <= prev[-1] <= "\u9fff")
            and ("\u4e00" <= p[0] <= "\u9fff")
        ):
            out[-1] = prev + p
        else:
            out.append(p)
    return " ".join(out).strip()


def _asr_has_speech(text: str) -> bool:
    for ch in text or "":
        if "\u4e00" <= ch <= "\u9fff" or ch.isalnum():
            return True
    return False


def transcribe_prompt_wav(path: str, cancel_flag=None) -> str:
    """用已有 Whisper 子进程把原声转成逐字稿，供 CosyVoice zero_shot。"""
    import importlib.util
    import numpy as np

    if importlib.util.find_spec("faster_whisper") is None:
        raise RuntimeError(
            "未安装 faster-whisper，无法识别原声文案。\n"
            "请退出程序后执行：pip install faster-whisper numpy"
        )
    audio, sr = load_wav_float32(path)
    if audio is None or int(sr or 0) <= 0:
        raise RuntimeError("无法读取原声音文件（需要 wav）")
    audio = np.asarray(audio, dtype="float32").reshape(-1)
    target = 16000
    if int(sr) != target and len(audio) > 1:
        n = max(1, int(round(len(audio) * float(target) / float(sr))))
        audio = np.interp(
            np.linspace(0, len(audio) - 1, n),
            np.arange(len(audio)),
            audio,
        ).astype("float32")
        sr = target
    from utils.voice_input import pick_default_model, transcribe_in_subprocess

    raw = transcribe_in_subprocess(
        audio,
        int(sr or target),
        model_size=pick_default_model(),
        language="zh",
        force_cpu=True,
        timeout=600,
        cancel_flag=cancel_flag,
    )
    text = _clean_asr_text(raw)
    if not _asr_has_speech(text):
        raise RuntimeError(
            "原声识别结果为空，没法走零样本克隆。请换一段更清楚的原声。"
        )
    return text


class ProbeWorker(QThread):
    """子进程探测 torch / CosyVoice，不挡 UI。"""

    done = pyqtSignal(dict)
    fail = pyqtSignal(str)

    def __init__(self, model_dir: str = "", parent=None):
        super().__init__(parent)
        self.model_dir = model_dir or default_model_dir()
        self.cosyvoice_home = find_cosyvoice_home()

    def run(self):
        try:
            data = _run_job(
                {
                    "action": "probe",
                    "model_dir": self.model_dir,
                    "cosyvoice_home": self.cosyvoice_home,
                },
                timeout=120,
            )
            self.done.emit(data if isinstance(data, dict) else {"ok": False})
        except Exception as e:
            self.fail.emit(f"{type(e).__name__}: {e}")


class SynthWorker(QThread):
    status = pyqtSignal(str)
    done = pyqtSignal(str, dict)  # out_path, info
    fail = pyqtSignal(str)

    def __init__(
        self,
        *,
        model_dir: str,
        prompt_wav: str,
        tts_text: str,
        prompt_text: str = "",
        instruct: str = "",
        speed: float = 1.0,
        out_path: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.model_dir = model_dir
        self.prompt_wav = prompt_wav
        self.tts_text = tts_text
        self.prompt_text = prompt_text
        self.instruct = instruct
        self.speed = speed
        self.out_path = out_path
        self._cancel = False
        self._procs = []

    def stop(self):
        self._cancel = True
        for proc in list(self._procs):
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.kill()
            except Exception:
                pass

    def run(self):
        try:
            if self._cancel:
                self.fail.emit("已取消")
                return
            out = self.out_path or new_out_wav_path(
                voice_name=os.path.splitext(os.path.basename(self.prompt_wav or ""))[0],
                tts_text=self.tts_text,
            )
            t0 = time.time()
            prompt_text = (self.prompt_text or "").strip()
            if not prompt_text:
                self.status.emit("正在用 Whisper 识别原声逐字稿…")
                prompt_text = transcribe_prompt_wav(
                    self.prompt_wav, cancel_flag=self._procs
                )
                if self._cancel:
                    self.fail.emit("已取消")
                    return
                self.prompt_text = prompt_text
                self.status.emit(f"原声文案已识别（{len(prompt_text)} 字），开始克隆…")
            if self._cancel:
                self.fail.emit("已取消")
                return
            self.status.emit("正在独立子进程加载 CosyVoice（首次较慢）…")
            data = _run_job(
                {
                    "action": "synth",
                    "model_dir": self.model_dir,
                    "cosyvoice_home": find_cosyvoice_home(),
                    "prompt_wav": self.prompt_wav,
                    "prompt_text": prompt_text,
                    "tts_text": self.tts_text,
                    "instruct": self.instruct or "",
                    "speed": float(self.speed or 1.0),
                    "out": out,
                },
                timeout=900,
                cancel_flag=self._procs,
            )
            if self._cancel:
                self.fail.emit("已取消")
                return
            path = (data.get("out") or out) if isinstance(data, dict) else out
            if not path or not os.path.isfile(path):
                raise RuntimeError("合成完成但找不到输出文件")
            info = dict(data or {})
            info["elapsed"] = round(time.time() - t0, 1)
            if prompt_text and not (info.get("prompt_text") or "").strip():
                info["prompt_text"] = prompt_text
            self.done.emit(path, info)
        except Exception as e:
            if self._cancel:
                self.fail.emit("已取消")
                return
            self.fail.emit(f"{type(e).__name__}: {e}")


class PlayWavWorker(QThread):
    """用 sounddevice 播 wav；没有该包时不要创建本线程。"""

    fail = pyqtSignal(str)
    done = pyqtSignal()

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        self._stop = False

    def stop(self):
        self._stop = True
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass

    def run(self):
        try:
            import sounddevice as sd
        except Exception:
            self.fail.emit("缺少 sounddevice，改用系统播放器")
            return
        try:
            audio, sr = load_wav_float32(self.path)
            if audio is None or sr <= 0:
                self.fail.emit("无法读取 wav")
                return
            sd.play(audio, sr)
            sd.wait()
            if not self._stop:
                self.done.emit()
        except Exception as e:
            self.fail.emit(f"{type(e).__name__}: {e}")
