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
            self.fail.emit(
                "缺少录音依赖 sounddevice / numpy。\n"
                "请执行：pip install sounddevice numpy"
            )
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
            self.fail.emit(
                f"录音失败：{type(e).__name__}: {e}\n"
                "请检查系统麦克风权限与默认录音设备。"
            )


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

    with tempfile.TemporaryDirectory(prefix="deskassist_whisper_") as td:
        out_path = os.path.join(td, "out.txt")
        err_path = os.path.join(td, "err.txt")
        meta = dict(build_meta(td) or {})
        meta["out"] = out_path
        meta["err"] = err_path
        meta_path = os.path.join(td, "meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)

        # 子进程必须用无 Qt 的 whisper_job（import PyQt 会触发 ctranslate2 硬崩）
        # 开发：python -m utils.whisper_job；打包：exe --whisper-job（mainv916 在 import Qt 前分流）
        try:
            from utils.app_paths import is_frozen
            frozen = is_frozen()
        except Exception:
            frozen = bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")
        env = os.environ.copy()
        env.setdefault("OMP_NUM_THREADS", "1")
        env.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
        env.pop("QT_PLUGIN_PATH", None)

        if frozen:
            from utils.deskassist_runtime import find_python, meipass
            _py = find_python()
            _worker = meipass() / "whisper_worker" / "run.py"
            if _py and _worker.is_file():
                cmd = [_py, str(_worker), meta_path]
                env["WHISPER_MEIPASS"] = str(meipass())
                env["OCR_MEIPASS"] = str(meipass())
            else:
                cmd = [sys.executable, "--whisper-job", meta_path]
        else:
            cmd = [sys.executable, "-m", "utils.whisper_job", meta_path]

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
        return open(out_path, encoding="utf-8", errors="replace").read().strip()


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
                self.fail.emit("录音太短，没有识别到有效语音。")
                return
            local_dir = local_model_dir(self.model_size)
            if os.path.isdir(local_dir) and os.listdir(local_dir):
                self.status.emit("正在子进程加载本地语音模型…")
            else:
                self.status.emit("本地无该模型，子进程可能联网下载（首次较慢）…")

            if self.use_subprocess:
                self.status.emit("正在识别语音（独立进程，避免崩溃）…")
                text = transcribe_in_subprocess(
                    self.audio,
                    self.samplerate,
                    model_size=self.model_size,
                    language=self.language,
                    force_cpu=self.force_cpu,
                )
            else:
                # 仅无 Qt 的环境可走进程内
                self.status.emit("正在识别语音…")
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
                self.fail.emit("找不到所选音频文件。")
                return
            local_dir = local_model_dir(self.model_size)
            if os.path.isdir(local_dir) and os.listdir(local_dir):
                self.status.emit("正在子进程加载本地语音模型…")
            else:
                self.status.emit("本地无该模型，子进程可能联网下载（首次较慢）…")
            self.status.emit("正在识别音频（独立进程，避免崩溃）…")
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
#   打包：mainv916.exe --whisper-job <meta.json>（main 在 import Qt 前分流）
# 不可用本模块作 -m 入口（顶层 import 了 PyQt5，会触发硬崩）
