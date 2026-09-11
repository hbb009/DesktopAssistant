# utils/whisper_job.py
# ---------------------------------------------------------------------------
# 无 Qt 的语音转写子进程入口。
# 切勿在本文件 import PyQt5 —— Windows 上 QApplication/Qt DLL 与
# ctranslate2/faster-whisper 同进程加载会导致 access violation。
#
# 调用：
#   python -m utils.whisper_job <meta.json>
# meta.json:
#   { "npy", "out", "err", "samplerate", "model_size", "language", "force_cpu" }
#   # 录音数组：npy 指向 float32 音频文件
#   # 或音频文件：file 指向本地声音文件（wav/mp3/m4a/flac…，PyAV 解码，
#   #             需安装 faster-whisper 自带依赖 av）；此时无需 npy/samplerate
# ---------------------------------------------------------------------------

from __future__ import annotations

import json
import os
import sys
import traceback


def _base_dir() -> str:
    """可写根：exe 旁 / 项目根（与 app_paths.app_root 一致）。"""
    try:
        from utils.app_paths import app_root
        return app_root()
    except Exception:
        if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
            return os.path.dirname(os.path.abspath(sys.executable))
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _local_model_dir(model_size: str) -> str:
    """本地模型：优先 exe 旁 model/，其次包内 resource。"""
    name = f"faster-whisper-{model_size}"
    beside = os.path.join(_base_dir(), "model", name)
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


def _is_chinese_lang(language) -> bool:
    return language in (None, "", "zh", "chinese", "zh-cn", "zh-CN", "zh-tw", "zh-hk")


def _transcribe_options(language):
    """转写参数：引导中文标点 + 简体（Whisper 常混繁体）。"""
    opts = {
        "language": language if language not in ("zh-cn", "zh-CN", "zh-tw", "zh-hk") else "zh",
        "vad_filter": True,
        "beam_size": 5,
        "condition_on_previous_text": True,
        "word_timestamps": False,
    }
    # language 为 None 时也按中文场景引导（本产品默认中文用户）
    if _is_chinese_lang(language):
        opts["initial_prompt"] = (
            "以下是简体中文普通话的句子，请只用简体字，不要用繁体字，"
            "并使用正确的中文标点符号，包括逗号、句号、问号、感叹号、顿号和引号。"
        )
    elif language in ("en", "english"):
        opts["initial_prompt"] = (
            "The following is a transcription in English with proper punctuation."
        )
    return opts


def to_simplified_chinese(text: str) -> str:
    """繁体 → 简体。优先 zhconv；未安装则原样返回。"""
    if not text:
        return text or ""
    try:
        from zhconv import convert
        return convert(text, "zh-cn")
    except Exception:
        return text


def transcribe_from_meta(meta_path: str) -> int:
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

    err_path = None
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)

        out = meta.get("out") or (str(meta.get("npy") or "") + ".txt")
        err_path = os.path.join(os.path.dirname(out) or ".", "err.txt")

        import numpy as np
        from faster_whisper import WhisperModel

        # 输入二选一：npy 录音数组，或 file 本地音频文件
        src_file = str(meta.get("file") or "").strip()
        if src_file:
            if not os.path.isfile(src_file):
                raise ValueError(f"找不到音频文件：{src_file}")
            from faster_whisper.audio import decode_audio
            audio = decode_audio(src_file)  # float32 单声道 16k
            sr = 16000
        else:
            audio = np.load(meta["npy"])
            audio = np.asarray(audio, dtype="float32").reshape(-1)
            sr = int(meta.get("samplerate") or 16000)
        if audio is None or len(audio) < int(sr * 0.3):
            raise ValueError("音频太短，没有识别到有效语音。")

        model_size = meta.get("model_size") or "tiny"
        language = meta.get("language")  # may be null
        force_cpu = bool(meta.get("force_cpu", True))

        local_dir = _local_model_dir(model_size)
        has_local = os.path.isdir(local_dir) and bool(os.listdir(local_dir))
        path_or_name = local_dir if has_local else model_size

        if force_cpu:
            model = WhisperModel(
                path_or_name,
                device="cpu",
                compute_type="int8",
                local_files_only=has_local,
            )
        else:
            try:
                model = WhisperModel(
                    path_or_name,
                    device="auto",
                    compute_type="auto",
                    local_files_only=has_local,
                )
            except Exception:
                model = WhisperModel(
                    path_or_name,
                    device="cpu",
                    compute_type="int8",
                    local_files_only=has_local,
                )

        segments, _info = model.transcribe(audio, **_transcribe_options(language))
        text = "".join(seg.text for seg in segments).strip()
        # Whisper 中文常出繁体：后处理统一简体
        if _is_chinese_lang(language):
            text = to_simplified_chinese(text)
        with open(out, "w", encoding="utf-8") as f:
            f.write(text or "")
        return 0
    except Exception:
        err = traceback.format_exc()
        try:
            sys.stderr.write(err)
        except Exception:
            pass
        if err_path:
            try:
                with open(err_path, "w", encoding="utf-8") as f:
                    f.write(err)
            except Exception:
                pass
        return 1


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv or not argv[0].endswith(".json"):
        print("usage: python -m utils.whisper_job <meta.json>", file=sys.stderr)
        return 2
    return transcribe_from_meta(argv[0])


if __name__ == "__main__":
    raise SystemExit(main())
