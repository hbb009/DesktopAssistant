# utils/cosyvoice_job.py
# ---------------------------------------------------------------------------
# 无 Qt 的 CosyVoice 子进程入口。
# 切勿在本文件 import PyQt5 —— Windows 上主进程已加载的 Qt / OpenMP
# 会与 PyTorch、onnxruntime 抢 DLL，表现为闪退或空 ImportError。
#
# 开发：python -m utils.cosyvoice_job <meta.json>
# 打包：mainv916.exe --cosyvoice-job <meta.json>
# meta.json:
#   {
#     "action": "probe" | "synth",
#     "model_dir": "...",
#     "cosyvoice_home": "...",
#     "prompt_wav": "...",
#     "prompt_text": "...",
#     "tts_text": "...",
#     "instruct": "...",
#     "speed": 1.0,
#     "out": "out.wav",
#     "result": "result.json"
#   }
# ---------------------------------------------------------------------------

from __future__ import annotations

import json
import os
import sys
import traceback


def _prepare_env() -> None:
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    os.environ["DESKASSIST_COSYVOICE_JOB"] = "1"
    os.environ.pop("QT_PLUGIN_PATH", None)
    os.environ.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)


def _write_json(path: str, data: dict) -> None:
    if not path:
        return
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _exc_text(err: BaseException) -> str:
    raw = str(err).strip()
    if raw:
        return f"{type(err).__name__}: {raw}"
    return f"{type(err).__name__}: {err!r}"


def _add_runtime_path(home: str) -> None:
    home = os.path.abspath(home or "")
    if not home or not os.path.isdir(home):
        return
    if home not in sys.path:
        sys.path.insert(0, home)
    matcha = os.path.join(home, "third_party", "Matcha-TTS")
    if os.path.isdir(matcha) and matcha not in sys.path:
        sys.path.insert(0, matcha)


def _patch_ort_cuda_fallback() -> None:
    """ORT GPU EP 在 CUDA_PATH 不完整（常见缺 cuDNN）时会硬失败。

    CosyVoice 前端只要 torch.cuda 可用就把 speech tokenizer 绑到
    CUDAExecutionProvider；ORT 内部 fallback 仍会再实例化 CUDA EP，
    所以必须自己 catch 后改走 CPU。音色主模型仍用 PyTorch CUDA。
    """
    try:
        import onnxruntime as ort
    except Exception:
        return
    orig = getattr(ort, "InferenceSession", None)
    if orig is None or getattr(orig, "_deskassist_cpu_fallback", False):
        return

    def _session(path, sess_options=None, providers=None, provider_options=None, **kwargs):
        try:
            return orig(
                path,
                sess_options=sess_options,
                providers=providers,
                provider_options=provider_options,
                **kwargs,
            )
        except Exception as e:
            names = []
            for p in providers or []:
                names.append(p[0] if isinstance(p, (tuple, list)) else str(p))
            if not any("CUDA" in str(n) for n in names):
                raise
            sys.stderr.write(f"[deskassist] ONNX CUDA EP 不可用，改用 CPU：{e}\n")
            return orig(
                path,
                sess_options=sess_options,
                providers=["CPUExecutionProvider"],
                **kwargs,
            )

    _session._deskassist_cpu_fallback = True
    ort.InferenceSession = _session


def _import_automodel(home: str):
    _add_runtime_path(home)
    _patch_ort_cuda_fallback()
    try:
        from cosyvoice.cli.cosyvoice import AutoModel
        return AutoModel
    except Exception:
        from cosyvoice.cli.cosyvoice import CosyVoice as AutoModel
        return AutoModel


def _call_with_known_kwargs(fn, *args, extra: dict):
    """只传入目标函数签名里存在的关键字，兼容 CosyVoice 2/3。"""
    try:
        import inspect
        params = inspect.signature(fn).parameters
        kwargs = {k: v for k, v in extra.items() if k in params}
    except Exception:
        kwargs = dict(extra)
    return fn(*args, **kwargs)


def _concat_speech(chunks) -> object:
    import torch

    pieces = []
    for item in chunks or []:
        if item is None:
            continue
        wav = item.get("tts_speech") if isinstance(item, dict) else item
        if wav is None:
            continue
        pieces.append(wav)
    if not pieces:
        raise RuntimeError("模型没有返回音频")
    if len(pieces) == 1:
        return pieces[0]
    return torch.cat(pieces, dim=-1)


def _save_wav(path: str, wav, sample_rate: int) -> None:
    """写成 16-bit PCM wav。

    torchaudio.save 对 float 张量默认写 IEEE float（format 3），
    Python wave / 不少播放器都打不开。
    """
    import torch

    if not isinstance(wav, torch.Tensor):
        wav = torch.as_tensor(wav)
    wav = wav.detach().cpu().float()
    if wav.ndim == 1:
        wav = wav.unsqueeze(0)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        import torchaudio
        torchaudio.save(
            path,
            wav,
            int(sample_rate),
            encoding="PCM_S",
            bits_per_sample=16,
        )
        return
    except Exception:
        pass
    import numpy as np
    import wave

    audio = wav.numpy()
    if audio.ndim == 2:
        audio = audio[0] if audio.shape[0] <= audio.shape[1] else audio[:, 0]
    audio = np.clip(audio, -1.0, 1.0)
    pcm = (audio * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm.tobytes())


def _do_probe(meta: dict) -> dict:
    import importlib.util

    home = (meta.get("cosyvoice_home") or "").strip()
    model_dir = (meta.get("model_dir") or "").strip()
    info = {
        "ok": False,
        "action": "probe",
        "model_dir": model_dir,
        "cosyvoice_home": home,
        "torch": "",
        "cuda": False,
        "gpu": "",
        "cosyvoice": False,
        "error": "",
    }
    if home:
        _add_runtime_path(home)

    torch_ok = importlib.util.find_spec("torch") is not None
    if not torch_ok:
        info["error"] = "未安装 PyTorch（torch / torchaudio）"
        return info
    import torch

    info["torch"] = str(getattr(torch, "__version__", "") or "")
    info["cuda"] = bool(torch.cuda.is_available())
    if info["cuda"]:
        try:
            info["gpu"] = torch.cuda.get_device_name(0)
        except Exception:
            info["gpu"] = "CUDA"

    try:
        _import_automodel(home)
        info["cosyvoice"] = True
    except Exception as e:
        info["error"] = f"无法导入 CosyVoice：{_exc_text(e)}"
        return info

    if model_dir and not os.path.isdir(model_dir):
        info["error"] = f"模型目录不存在：{model_dir}"
        return info
    info["ok"] = True
    return info


_CV3_EOP = "You are a helpful assistant.<|endofprompt|>"


def _is_cosyvoice3(model_dir: str) -> bool:
    return bool(model_dir) and os.path.isfile(os.path.join(model_dir, "cosyvoice3.yaml"))


def _wrap_cv3(text: str) -> str:
    """CosyVoice3 要求输入里出现 <|endofprompt|>。"""
    raw = (text or "").strip()
    if "<|endofprompt|>" in raw:
        return raw
    return _CV3_EOP + raw


def _wrap_prompt_text(raw: str) -> str:
    """参考音频的逐字稿。空串表示没有稿，不要填演示例句。"""
    text = (raw or "").strip()
    if not text:
        return ""
    if "<|endofprompt|>" in text:
        return text
    return _CV3_EOP + text


def _wrap_instruct(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    if "<|endofprompt|>" in text:
        return text
    if text.startswith("You are a helpful assistant"):
        return text if text.endswith("<|endofprompt|>") else text + "<|endofprompt|>"
    return f"You are a helpful assistant. {text}<|endofprompt|>"


def _split_tts_chunks(model, tts_text: str) -> list:
    """先按官方 frontend 切句；失败则整段一条。"""
    text = (tts_text or "").strip()
    if not text:
        return []
    try:
        frontend = getattr(model, "frontend", None)
        if frontend is not None and hasattr(frontend, "text_normalize"):
            chunks = frontend.text_normalize(text, split=True, text_frontend=True)
            out = [str(c).strip() for c in (chunks or []) if str(c).strip()]
            if out:
                return out
    except Exception:
        pass
    return [text]


def _synth_cross_lingual(model, tts_text: str, prompt_wav: str, extra: dict, *, cv3: bool):
    """没有参考音频逐字稿时走 cross_lingual：LLM 只看要说的字，音色走 speaker embedding。

    旧逻辑把演示稿「希望你以后能够做的比我还好呦。」塞进 prompt_text，
    模型会按那句（或按参考音频里的原话）念，而不是用户写的内容。
    """
    chunks = _split_tts_chunks(model, tts_text)
    pieces = []
    call_extra = dict(extra)
    if cv3:
        call_extra["text_frontend"] = False
    for chunk in chunks:
        piece = _wrap_cv3(chunk) if cv3 else chunk
        gen = _call_with_known_kwargs(
            model.inference_cross_lingual,
            piece,
            prompt_wav,
            extra=call_extra,
        )
        pieces.extend(list(gen))
    return pieces


def _wav_seconds(path: str) -> float:
    """用标准库 wave 读 wav 时长；读不到返回 0。"""
    try:
        import wave

        with wave.open(path, "rb") as w:
            n = int(w.getnframes() or 0)
            r = int(w.getframerate() or 0)
            return (float(n) / float(r)) if r > 0 else 0.0
    except Exception:
        return 0.0


def _do_synth(meta: dict) -> dict:
    home = (meta.get("cosyvoice_home") or "").strip()
    model_dir = (meta.get("model_dir") or "").strip()
    prompt_wav = (meta.get("prompt_wav") or "").strip()
    tts_text = (meta.get("tts_text") or "").strip()
    out = (meta.get("out") or "").strip()
    instruct = _wrap_instruct(meta.get("instruct") or "")
    prompt_text_raw = (meta.get("prompt_text") or "").strip()
    try:
        speed = float(meta.get("speed") or 1.0)
    except (TypeError, ValueError):
        speed = 1.0
    speed = max(0.5, min(2.0, speed))

    if not model_dir or not os.path.isdir(model_dir):
        raise FileNotFoundError(f"模型目录不存在：{model_dir}")
    if not prompt_wav or not os.path.isfile(prompt_wav):
        raise FileNotFoundError("请先选择或录制参考音频")
    # CosyVoice frontend 硬性要求参考音频 ≤30s（frontend.py assert），
    # 提前拦截，避免加载完模型才抛晦涩的 AssertionError。
    dur = _wav_seconds(prompt_wav)
    if dur and dur > 30.0:
        raise RuntimeError(
            f"参考音频太长（{dur:.1f} 秒）。CosyVoice 只支持 30 秒以内的原声，"
            "请先裁剪到 30 秒以内再试。"
        )
    if not tts_text:
        raise ValueError("请输入要合成的文字")
    if not out:
        raise ValueError("未指定输出文件")

    AutoModel = _import_automodel(home)
    import torch

    fp16 = bool(torch.cuda.is_available())
    # 本仓库 AutoModel 是函数，且 CosyVoice3.__init__ 没有 load_jit
    extra_init = {"fp16": fp16, "load_trt": False, "load_vllm": False}
    try:
        model = AutoModel(model_dir=model_dir, **extra_init)
    except TypeError:
        model = AutoModel(model_dir, **extra_init)
    sample_rate = int(getattr(model, "sample_rate", 24000) or 24000)
    cv3 = _is_cosyvoice3(model_dir)

    call_extra = {"stream": False, "speed": speed, "text_frontend": True}
    if instruct:
        gen = _call_with_known_kwargs(
            model.inference_instruct2,
            tts_text,
            instruct,
            prompt_wav,
            extra=call_extra,
        )
        mode = "instruct2"
        wav = _concat_speech(list(gen))
    elif prompt_text_raw:
        prompt_text = _wrap_prompt_text(prompt_text_raw) if cv3 else prompt_text_raw
        gen = _call_with_known_kwargs(
            model.inference_zero_shot,
            tts_text,
            prompt_text,
            prompt_wav,
            extra=call_extra,
        )
        mode = "zero_shot"
        wav = _concat_speech(list(gen))
    else:
        mode = "cross_lingual"
        wav = _concat_speech(
            _synth_cross_lingual(model, tts_text, prompt_wav, call_extra, cv3=cv3)
        )
    parent = os.path.dirname(out)
    if parent:
        os.makedirs(parent, exist_ok=True)
    _save_wav(out, wav, sample_rate)
    if not os.path.isfile(out) or os.path.getsize(out) < 64:
        raise RuntimeError("合成文件未写出或过小")
    return {
        "ok": True,
        "action": "synth",
        "out": out,
        "sample_rate": sample_rate,
        "mode": mode,
        "fp16": fp16,
        "cuda": bool(torch.cuda.is_available()),
        "prompt_text": prompt_text_raw,
    }


def run_meta(meta_path: str) -> int:
    _prepare_env()
    result_path = ""
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        if not isinstance(meta, dict):
            raise ValueError("meta.json 不是对象")
        result_path = (meta.get("result") or "").strip()
        action = str(meta.get("action") or "synth").strip().lower()
        if action == "probe":
            data = _do_probe(meta)
        elif action == "synth":
            data = _do_synth(meta)
        else:
            data = {"ok": False, "error": f"未知 action: {action}"}
        if result_path:
            _write_json(result_path, data)
        if not data.get("ok"):
            err = data.get("error") or "失败"
            sys.stderr.write(err + "\n")
            return 1
        return 0
    except Exception:
        err = traceback.format_exc()
        try:
            sys.stderr.write(err)
        except Exception:
            pass
        if result_path:
            try:
                _write_json(result_path, {"ok": False, "error": err[-4000:]})
            except Exception:
                pass
        return 1


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv or not str(argv[0]).lower().endswith(".json"):
        print("usage: python -m utils.cosyvoice_job <meta.json>", file=sys.stderr)
        return 2
    return run_meta(argv[0])


if __name__ == "__main__":
    raise SystemExit(main())
