# utils/voice_input.py
# 本地语音转文字（faster-whisper + sounddevice）
# 供「语音录入」页与 voice_input_test.py 共用
#
# 重要：不要在模块顶层 import faster_whisper / sounddevice。
# faster-whisper 会连带加载 ctranslate2 等，单次 import 可达十余秒，
# 若在主程序启动路径上 import 本模块，会把整机启动拖成「奇慢」。

from __future__ import annotations

import importlib.util
import os
import sys

from PyQt5.QtCore import QThread, pyqtSignal


def _pkg_available(name: str) -> bool:
    """只探测包是否安装，不真正 import（避免重库拖慢启动）。"""
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _refresh_pkg_finders() -> None:
    """pip 在进程内装完包后，必须清 finder 缓存，否则 find_spec 仍可能报「没有」。"""
    try:
        importlib.invalidate_caches()
    except Exception:
        pass


# 轻量探测：find_spec 几乎瞬时；真正 import 推迟到录音/转写时
HAS_SOUNDDEVICE = _pkg_available("sounddevice") and _pkg_available("numpy")
HAS_FASTER_WHISPER = _pkg_available("faster_whisper")

SAMPLE_RATE = 16000  # whisper 原生 16k 单声道

# (目录后缀 / 规格名, 展示文案)
MODEL_CHOICES = [
    ("tiny", "tiny · 最快"),
    ("small", "small · 推荐"),
    ("large-v3", "large-v3 · 最准"),
]

LANGUAGE_CHOICES = [
    (None, "自动检测"),
    ("zh", "中文"),
    ("en", "英文"),
]

# 进程内缓存 WhisperModel，避免每次转写重载
_MODEL_CACHE = {}


def base_dir() -> str:
    """可写根（exe 旁 / 项目根）；模型可放在旁边的 model/。"""
    try:
        from utils.app_paths import app_root
        return app_root()
    except Exception:
        return getattr(
            sys, "_MEIPASS",
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )


def ve_op_msg(action: str, *paths, extra: str = "") -> str:
    """操作记录一行：VoiceEdit 复制 · voice_xxx.wav"""
    names = []
    for p in paths:
        n = os.path.basename((p or "").strip())
        if n and n not in names:
            names.append(n)
    action = (action or "").strip() or "操作"
    if action == "转码" and len(names) >= 2:
        body = f"{names[0]} → {names[1]}"
    else:
        body = "、".join(names)
    parts = [f"VoiceEdit {action}"]
    if body:
        parts.append(body)
    extra = (extra or "").strip()
    if extra:
        parts.append(extra)
    return " · ".join(parts)


def work_dir() -> str:
    """语音录入输入/输出目录：程序根 \\VoiceEdit。"""
    try:
        from utils.app_paths import voice_edit_dir
        return voice_edit_dir()
    except Exception:
        d = os.path.join(base_dir(), "VoiceEdit")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        return d


def _stamp() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _safe_name(name: str) -> str:
    base = os.path.basename(name or "") or "audio"
    out = []
    for ch in base:
        if ch.isalnum() or ch in "._- ()[]{}+":
            out.append(ch)
        else:
            out.append("_")
    s = "".join(out).strip(" ._") or "audio"
    return s[:80]


def save_recording_wav(audio, samplerate: int, dest_wav: str = "") -> str:
    """把内存录音写成 wav。dest_wav 为空则 VoiceEdit/rec_时间戳.wav。失败返回空串。"""
    try:
        import numpy as np
        import wave
    except Exception:
        return ""
    if audio is None:
        return ""
    samples = np.asarray(audio, dtype="float32").reshape(-1)
    if samples.size <= 0:
        return ""
    path = (dest_wav or "").strip() or os.path.join(work_dir(), f"rec_{_stamp()}.wav")
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(int(samplerate or SAMPLE_RATE))
            w.writeframes(pcm.tobytes())
        return path
    except Exception:
        return ""


def copy_input_audio(src: str, prefix: str = "in") -> str:
    """把用户选的音频复制进 VoiceEdit，失败则返回原路径。

    prefix：voice=克隆声音素材。转写上传不再复制原文件。
    """
    import shutil
    src = os.path.normpath(src or "")
    if not src or not os.path.isfile(src):
        return src
    d = work_dir()
    try:
        if os.path.normcase(os.path.abspath(os.path.dirname(src))) == os.path.normcase(
            os.path.abspath(d)
        ):
            return src
    except Exception:
        pass
    tag = (prefix or "in").strip() or "in"
    dest = os.path.join(d, f"{tag}_{_stamp()}_{_safe_name(src)}")
    try:
        shutil.copy2(src, dest)
        return dest
    except Exception:
        try:
            from utils.logger import get_logger
            get_logger("voice_input").exception(
                "复制音频到 VoiceEdit 失败 src=%s dest=%s", src, dest
            )
        except Exception:
            pass
        return src


def audio_duration_sec(path: str) -> float:
    """读 wav 头得到秒数；非 wav / 失败返回 0。"""
    path = (path or "").strip()
    if not path or not os.path.isfile(path):
        return 0.0
    if os.path.splitext(path)[1].lower() != ".wav":
        return 0.0
    try:
        import wave
        with wave.open(path, "rb") as w:
            n = int(w.getnframes() or 0)
            r = int(w.getframerate() or 0)
        if n > 0 and r > 0:
            return float(n) / float(r)
    except Exception:
        pass
    return 0.0


def transcript_path_for(audio_path: str) -> str:
    """声音文件对应的同目录同名 .txt。"""
    audio_path = (audio_path or "").strip()
    if not audio_path:
        return ""
    stem, _ext = os.path.splitext(audio_path)
    return (stem + ".txt") if stem else ""


_AUDIO_EXTS = {
    ".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma",
    ".mp4", ".m4v", ".webm", ".mkv",
}
_SKIP_TXT_NAMES = frozenset({"out.txt", "err.txt", "meta.txt"})
_OLD_PAIR_SEC = 600  # 旧版 rec_时间戳.wav 与 out_时间戳.txt 允许相差的秒数


def _read_transcript(path: str) -> str:
    path = (path or "").strip()
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except Exception:
        return ""


def _name_stamp(name: str) -> str:
    """从 rec_/in_/out_YYYYMMDD_HHMMSS… 取出时间戳。"""
    base = os.path.splitext(os.path.basename(name or ""))[0]
    lower = base.lower()
    for prefix in ("rec_", "in_", "out_"):
        if not lower.startswith(prefix):
            continue
        rest = base[len(prefix):]
        if len(rest) >= 15 and rest[8] == "_" and rest[:8].isdigit() and rest[9:15].isdigit():
            return rest[:15]
    return ""


def _stamp_epoch(stamp: str) -> int:
    if not stamp:
        return 0
    try:
        from datetime import datetime
        return int(datetime.strptime(stamp, "%Y%m%d_%H%M%S").timestamp())
    except Exception:
        return 0


def _file_mtime(path: str) -> float:
    try:
        return float(os.path.getmtime(path))
    except Exception:
        return 0.0


def list_voice_edit_records() -> list:
    """扫描 VoiceEdit：同名音文成对（含旧版 rec_ / out_ 邻近配对），按时间升序。

    每项：audio_path, txt_path, text, duration, mtime
    """
    d = work_dir()
    if not os.path.isdir(d):
        return []
    try:
        names = os.listdir(d)
    except Exception:
        return []

    files = []
    for name in names:
        if not name or name.startswith("."):
            continue
        if name.lower().startswith("job_"):
            continue
        path = os.path.join(d, name)
        if not os.path.isfile(path):
            continue
        files.append(name)

    used_txt = set()
    rows = []
    audio_names = [
        n for n in files if os.path.splitext(n)[1].lower() in _AUDIO_EXTS
    ]
    for name in audio_names:
        lower = name.lower()
        if lower.startswith("voice_"):
            continue
        path = os.path.join(d, name)
        stem, _ext = os.path.splitext(name)
        txt_name = stem + ".txt"
        txt_path = os.path.join(d, txt_name)
        text = ""
        if os.path.isfile(txt_path):
            used_txt.add(txt_name.lower())
            text = _read_transcript(txt_path)
        else:
            txt_path = ""
        # 转写结果只收：录音 rec_*，或已写出同名 txt 的上传。
        # 克隆素材（voice_/无 txt 的 in_）不当成录音。
        if not txt_path and not lower.startswith("rec_"):
            continue
        rows.append({
            "audio_path": path,
            "txt_path": txt_path,
            "text": text,
            "duration": audio_duration_sec(path),
            "mtime": _file_mtime(path),
            "name": name,
        })

    leftover_txt = []
    for name in files:
        if os.path.splitext(name)[1].lower() != ".txt":
            continue
        if name.lower() in used_txt or name.lower() in _SKIP_TXT_NAMES:
            continue
        leftover_txt.append(name)

    # 旧版：rec_时间戳.wav 与稍后写出的 out_时间戳_audio.txt 不成对同名
    unmatched_audio = [r for r in rows if not r.get("txt_path")]
    unused_out = [n for n in leftover_txt if n.lower().startswith("out_")]
    unused_out.sort(key=lambda n: (_name_stamp(n), n.lower()))
    used_out = set()
    for rec in unmatched_audio:
        rec_st = _stamp_epoch(_name_stamp(rec.get("name") or ""))
        if rec_st <= 0:
            continue
        best = ""
        best_dt = None
        for name in unused_out:
            if name in used_out:
                continue
            out_st = _stamp_epoch(_name_stamp(name))
            if out_st <= 0:
                continue
            dt = out_st - rec_st
            if dt < 0 or dt > _OLD_PAIR_SEC:
                continue
            if best_dt is None or dt < best_dt:
                best, best_dt = name, dt
        if not best:
            continue
        used_out.add(best)
        used_txt.add(best.lower())
        txt_path = os.path.join(d, best)
        rec["txt_path"] = txt_path
        rec["text"] = _read_transcript(txt_path)
        rec["mtime"] = max(float(rec.get("mtime") or 0), _file_mtime(txt_path))

    for name in leftover_txt:
        if name.lower() in used_txt:
            continue
        path = os.path.join(d, name)
        text = _read_transcript(path)
        if not text:
            continue
        rows.append({
            "audio_path": "",
            "txt_path": path,
            "text": text,
            "duration": 0.0,
            "mtime": _file_mtime(path),
            "name": name,
        })

    rows.sort(key=lambda r: (float(r.get("mtime") or 0), str(r.get("name") or "").lower()))
    for r in rows:
        r.pop("name", None)
    return rows


def delete_voice_edit_record(audio_path: str = "", txt_path: str = "") -> list:
    """只删 VoiceEdit 里这条记录对应的声音 / 文本，不碰目录外文件。

    返回实际删掉的路径（已按出现顺序去重）。
    """
    removed = []
    try:
        root = os.path.normcase(os.path.abspath(work_dir()))
    except Exception:
        return removed
    seen = set()
    paths = [audio_path, txt_path]
    if audio_path:
        paths.append(transcript_path_for(audio_path))
    for p in paths:
        p = os.path.normpath((p or "").strip())
        if not p or p in seen:
            continue
        seen.add(p)
        try:
            ap = os.path.normcase(os.path.abspath(p))
        except Exception:
            continue
        if ap != root and not ap.startswith(root + os.sep):
            continue
        if not os.path.isfile(p):
            continue
        try:
            os.remove(p)
        except Exception:
            continue
        removed.append(p)
    return removed


def save_transcript_beside(text: str, audio_path: str) -> str:
    """把转写结果写成与声音文件同目录、同名的 .txt。失败返回空串。"""
    body = (text or "").strip()
    path = transcript_path_for(audio_path)
    if not body or not path:
        return ""
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
            if not body.endswith("\n"):
                f.write("\n")
        return path
    except Exception:
        return ""


def save_transcript_versioned(text: str, src_audio: str) -> str:
    """把转写结果写到 VoiceEdit/{原文件名}_{版本}.txt，版本从 01 起。

    不复制、不改动原声音文件。同名已存在则 02、03… 递增。
    """
    body = (text or "").strip()
    if not body:
        return ""
    stem = os.path.splitext(os.path.basename(src_audio or ""))[0]
    stem = _safe_name(stem)
    if not stem:
        stem = "audio"
    folder = work_dir()
    n = 1
    dest = ""
    while n < 1000:
        dest = os.path.join(folder, f"{stem}_{n:02d}.txt")
        if not os.path.exists(dest):
            break
        n += 1
    else:
        dest = os.path.join(folder, f"{stem}_{_stamp()}.txt")
    try:
        os.makedirs(folder, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(body)
            if not body.endswith("\n"):
                f.write("\n")
        return dest
    except Exception:
        return ""


def save_transcript_txt(text: str, src_name: str = "") -> str:
    """兼容旧调用：src_name 是完整路径则写同名 txt，否则 VoiceEdit/out_时间戳.txt。"""
    body = (text or "").strip()
    if not body:
        return ""
    src = (src_name or "").strip()
    if src and (os.path.isabs(src) or os.path.sep in src or (
        os.path.altsep and os.path.altsep in src
    )):
        return save_transcript_beside(body, src)
    stem = os.path.splitext(_safe_name(src))[0] if src else ""
    name = f"out_{_stamp()}"
    if stem:
        name = f"{name}_{stem}"
    path = os.path.join(work_dir(), f"{name}.txt")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
            if not body.endswith("\n"):
                f.write("\n")
        return path
    except Exception:
        return ""


def local_model_dir(model_size: str) -> str:
    """本地模型：model/faster-whisper-<size>/（优先 exe 旁，其次包内）。"""
    name = f"faster-whisper-{model_size}"
    beside = os.path.join(base_dir(), "model", name)
    if os.path.isdir(beside):
        return beside
    try:
        from utils.app_paths import resource_path
        bundled = resource_path("model", name)
        if os.path.isdir(bundled):
            return bundled
    except Exception:
        pass
    return beside


def scan_local_models() -> list:
    """返回本地已就绪的模型规格 key 列表（按 MODEL_CHOICES 顺序）。"""
    found = []
    for key, _ in MODEL_CHOICES:
        d = local_model_dir(key)
        if os.path.isdir(d) and os.listdir(d):
            found.append(key)
    return found


def pick_default_model() -> str:
    """优先 small，否则第一个本地模型，再否则 small（可能需联网）。"""
    local = scan_local_models()
    for prefer in ("small", "tiny", "large-v3"):
        if prefer in local:
            return prefer
    return "small"


def voice_deps_ok() -> tuple:
    """(ok, missing_packages_list)。可重复调用，会重新 find_spec。

    进程内刚 pip 装完时也会 invalidate_caches，避免语音页仍显示「缺少依赖」。
    """
    global HAS_SOUNDDEVICE, HAS_FASTER_WHISPER
    _refresh_pkg_finders()
    HAS_SOUNDDEVICE = _pkg_available("sounddevice") and _pkg_available("numpy")
    HAS_FASTER_WHISPER = _pkg_available("faster_whisper")
    missing = []
    if not HAS_SOUNDDEVICE:
        missing.append("sounddevice")
        missing.append("numpy")
    if not HAS_FASTER_WHISPER:
        missing.append("faster-whisper")
    return (len(missing) == 0, missing)


class RecordWorker(QThread):
    """持续录音到内存，stop() 后发出 float32 单声道数组。"""

    finished_audio = pyqtSignal(object, int)  # audio, samplerate
    fail = pyqtSignal(str)

    def __init__(self, samplerate: int = SAMPLE_RATE, parent=None):
        super().__init__(parent)
        self.samplerate = samplerate
        self._frames = []
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def run(self):
        # 此处才真正 import，不挡主程序启动
        try:
            import numpy as np
            import sounddevice as sd
        except Exception:
            self.fail.emit("缺少 sounddevice / numpy")
            return
        try:
            def _cb(indata, frames, time_info, status):
                if not self._stop_flag:
                    self._frames.append(indata.copy())

            with sd.InputStream(
                samplerate=self.samplerate,
                channels=1,
                dtype="float32",
                callback=_cb,
            ):
                while not self._stop_flag:
                    self.msleep(50)

            if self._frames:
                audio = np.concatenate(self._frames, axis=0).reshape(-1)
            else:
                audio = np.zeros(0, dtype="float32")
            self.finished_audio.emit(audio, self.samplerate)
        except Exception as e:
            self.fail.emit(f"录音失败：{e}")


def load_whisper_model(model_size: str = "small", *, force_cpu: bool = True):
    """在「无 QApplication」的进程里加载 WhisperModel。

    注意：Windows 上若已创建 QApplication，再 WhisperModel(...) 可能直接
    access violation 进程消失。主程序必须通过子进程调用本函数。
    """
    from faster_whisper import WhisperModel

    model_size = model_size or "small"
    cache_key = f"{model_size}|cpu={1 if force_cpu else 0}"
    if cache_key in _MODEL_CACHE:
        return _MODEL_CACHE[cache_key]

    local_dir = local_model_dir(model_size)
    has_local = os.path.isdir(local_dir) and bool(os.listdir(local_dir))
    model_path_or_name = local_dir if has_local else model_size

    if force_cpu:
        model = WhisperModel(
            model_path_or_name,
            device="cpu",
            compute_type="int8",
            local_files_only=has_local,
        )
    else:
        try:
            model = WhisperModel(
                model_path_or_name,
                device="auto",
                compute_type="auto",
                local_files_only=has_local,
            )
        except Exception:
            model = WhisperModel(
                model_path_or_name,
                device="cpu",
                compute_type="int8",
                local_files_only=has_local,
            )
    _MODEL_CACHE[cache_key] = model
    return model


def transcribe_audio_array(
    audio,
    samplerate: int = SAMPLE_RATE,
    model_size: str = "small",
    language: str = "zh",
    *,
    force_cpu: bool = True,
    beam_size: int = 5,
) -> str:
    """在当前进程转写（调用方须保证进程内尚未/不会与 Qt 冲突）。"""
    import numpy as np

    if audio is None:
        return ""
    audio = np.asarray(audio, dtype="float32").reshape(-1)
    if len(audio) < int(float(samplerate or SAMPLE_RATE) * 0.3):
        raise ValueError("录音太短，没有识别到有效语音。")

    model = load_whisper_model(model_size, force_cpu=force_cpu)
    # 与 whisper_job 保持一致：简体 + 标点
    from utils.whisper_job import _transcribe_options, to_simplified_chinese, _is_chinese_lang

    opts = _transcribe_options(language)
    opts["beam_size"] = beam_size
    segments, _info = model.transcribe(audio, **opts)
    text = "".join(seg.text for seg in segments).strip()
    if _is_chinese_lang(language):
        text = to_simplified_chinese(text)
    return text


def _run_whisper_job(
    build_meta,
    *,
    timeout: int = 600,
    cancel_flag=None,
) -> str:
    """把识别元信息写进临时目录，并启动无 Qt 子进程，返回识别文本。

    build_meta(td) 返回 dict：须含 model_size / language / force_cpu，
    并可按需把 npy（录音数组）或 file（音频文件）放进 td 供子进程读取。
    out / err 路径由本函数统一接管。
    """
    import json
    import subprocess
    import tempfile

    td_kw = {"prefix": "job_"}
    try:
        root = work_dir()
        os.makedirs(root, exist_ok=True)
        td_kw["dir"] = root
    except Exception:
        td_kw["prefix"] = "deskassist_whisper_"
    with tempfile.TemporaryDirectory(**td_kw) as td:
        out_path = os.path.join(td, "out.txt")
        err_path = os.path.join(td, "err.txt")
        meta = dict(build_meta(td) or {})
        meta["out"] = out_path
        meta["err"] = err_path
        meta_path = os.path.join(td, "meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)

        # 子进程必须用无 Qt 的 whisper_job（import PyQt 会触发 ctranslate2 硬崩）
        # 开发：python -m utils.whisper_job；打包：exe --whisper-job（mainv918 在 import Qt 前分流）
        try:
            from utils.app_paths import is_frozen
            frozen = is_frozen()
        except Exception:
            frozen = bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")
        if frozen:
            cmd = [sys.executable, "--whisper-job", meta_path]
        else:
            cmd = [sys.executable, "-m", "utils.whisper_job", meta_path]
        env = os.environ.copy()
        env.setdefault("OMP_NUM_THREADS", "1")
        env.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
        env.pop("QT_PLUGIN_PATH", None)

        # Windows：隐藏子进程黑框（python.exe 默认会闪一下 CMD）
        popen_kw = dict(
            cwd=base_dir(),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if sys.platform == "win32":
            # 0x08000000 = CREATE_NO_WINDOW
            popen_kw["creationflags"] = getattr(
                subprocess, "CREATE_NO_WINDOW", 0x08000000
            )
            try:
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = 0  # SW_HIDE
                popen_kw["startupinfo"] = si
            except Exception:
                pass

        proc = subprocess.Popen(cmd, **popen_kw)
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
            raise RuntimeError(f"识别子进程超时（{timeout}s）")
        if proc.returncode != 0:
            detail = ((stderr or "") + "\n" + (stdout or "")).strip()
            if os.path.isfile(err_path):
                try:
                    detail = (
                        open(err_path, encoding="utf-8", errors="replace")
                        .read()
                        .strip()
                        or detail
                    )
                except Exception:
                    pass
            # Windows access violation 常见退出码
            if int(proc.returncode or 0) in (-1073741819, 3221225477):
                detail = (
                    (detail + "\n") if detail else ""
                ) + "子进程 access violation（请确认 whisper_job 未 import PyQt5）"
            raise RuntimeError(
                f"识别子进程失败 code={proc.returncode}\n{detail[-1500:]}"
            )
        if not os.path.isfile(out_path):
            raise RuntimeError("识别子进程未写出结果文件")
        text = open(out_path, encoding="utf-8", errors="replace").read().strip()
        return text


def transcribe_in_subprocess(
    audio,
    samplerate: int = SAMPLE_RATE,
    model_size: str = "small",
    language: str = "zh",
    *,
    force_cpu: bool = True,
    timeout: int = 600,
    cancel_flag=None,
) -> str:
    """在无 Qt 的子进程中转写录音数组，避免 Windows 上 QApplication + ctranslate2 硬崩。

    cancel_flag 若是 list，会把子进程对象 append 进去，便于外部 terminate。
    """
    import numpy as np

    if audio is None:
        raise ValueError("录音为空")
    audio = np.asarray(audio, dtype="float32").reshape(-1)
    if len(audio) < int(float(samplerate or SAMPLE_RATE) * 0.3):
        raise ValueError("录音太短，没有识别到有效语音。")

    def _build(td):
        npy_path = os.path.join(td, "audio.npy")
        np.save(npy_path, audio)
        return {
            "npy": npy_path,
            "samplerate": int(samplerate or SAMPLE_RATE),
            "model_size": model_size or "small",
            "language": language,
            "force_cpu": bool(force_cpu),
        }

    return _run_whisper_job(_build, timeout=timeout, cancel_flag=cancel_flag)


def transcribe_file_in_subprocess(
    audio_file: str,
    model_size: str = "small",
    language: str = "zh",
    *,
    force_cpu: bool = True,
    timeout: int = 1800,
    cancel_flag=None,
) -> str:
    """在无 Qt 子进程中转写本地音频文件（wav/mp3/m4a/flac…）。

    解码交给 whisper_job 内的 faster-whisper（PyAV），主进程不加载重库。
    """
    audio_file = (audio_file or "").strip()
    if not os.path.isfile(audio_file):
        raise ValueError("找不到所选音频文件。")

    def _build(td):
        return {
            "file": audio_file,
            "model_size": model_size or "small",
            "language": language,
            "force_cpu": bool(force_cpu),
        }

    return _run_whisper_job(_build, timeout=timeout, cancel_flag=cancel_flag)


class TranscribeWorker(QThread):
    """本地 faster-whisper 转写（默认走子进程，避免 Qt 进程内加载模型崩溃）。"""

    status = pyqtSignal(str)
    done = pyqtSignal(str)
    fail = pyqtSignal(str)

    def __init__(
        self,
        audio,
        samplerate: int,
        model_size: str = "small",
        language: str = "zh",
        parent=None,
        *,
        force_cpu: bool = True,
        use_subprocess: bool = True,
    ):
        super().__init__(parent)
        self.audio = audio
        self.samplerate = samplerate
        self.model_size = model_size or "small"
        self.language = language  # None = 自动
        self.force_cpu = bool(force_cpu)
        # Windows + QApplication 下进程内 WhisperModel 会 access violation
        self.use_subprocess = bool(use_subprocess)

    def run(self):
        try:
            if self.audio is None or len(self.audio) < int(self.samplerate * 0.3):
                self.fail.emit("录音太短")
                return
            local_dir = local_model_dir(self.model_size)
            if os.path.isdir(local_dir) and os.listdir(local_dir):
                self.status.emit("加载模型…")
            else:
                self.status.emit("下载模型…")

            if self.use_subprocess:
                self.status.emit("识别中…")
                text = transcribe_in_subprocess(
                    self.audio,
                    self.samplerate,
                    model_size=self.model_size,
                    language=self.language,
                    force_cpu=self.force_cpu,
                )
            else:
                # 仅无 Qt 的环境可走进程内
                self.status.emit("识别中…")
                text = transcribe_audio_array(
                    self.audio,
                    self.samplerate,
                    model_size=self.model_size,
                    language=self.language,
                    force_cpu=self.force_cpu,
                )
            self.done.emit(text or "")
        except Exception as e:
            self.fail.emit(f"识别失败：{type(e).__name__}: {e}")


class TranscribeFileWorker(QThread):
    """本地音频文件（wav/mp3/m4a/flac…）→ faster-whisper 子进程转文字。"""

    status = pyqtSignal(str)
    done = pyqtSignal(str)
    fail = pyqtSignal(str)

    def __init__(
        self,
        audio_file: str,
        model_size: str = "small",
        language: str = "zh",
        parent=None,
        *,
        force_cpu: bool = True,
    ):
        super().__init__(parent)
        self.audio_file = (audio_file or "").strip()
        self.model_size = model_size or "small"
        self.language = language  # None = 自动
        self.force_cpu = bool(force_cpu)

    def run(self):
        try:
            if not self.audio_file or not os.path.isfile(self.audio_file):
                self.fail.emit("找不到音频文件")
                return
            local_dir = local_model_dir(self.model_size)
            if os.path.isdir(local_dir) and os.listdir(local_dir):
                self.status.emit("加载模型…")
            else:
                self.status.emit("下载模型…")
            self.status.emit("识别中…")
            text = transcribe_file_in_subprocess(
                self.audio_file,
                model_size=self.model_size,
                language=self.language,
                force_cpu=self.force_cpu,
            )
            self.done.emit(text or "")
        except Exception as e:
            self.fail.emit(f"识别失败：{type(e).__name__}: {e}")


# 子进程入口：
#   开发：python -m utils.whisper_job <meta.json>
#   打包：mainv918.exe --whisper-job <meta.json>（main 在 import Qt 前分流）
# 不可用本模块作 -m 入口（顶层 import 了 PyQt5，会触发硬崩）
