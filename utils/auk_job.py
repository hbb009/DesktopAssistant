# utils/auk_job.py
# ---------------------------------------------------------------------------
# 无 Qt 的 AuK 子进程。切勿 import PyQt5。
# 开发：python -m utils.auk_job --serve
# 打包：mainv918.exe --auk-job --serve
# 协议：stdin 一行一个 JSON 请求，stdout 一行一个 JSON 结果。日志走 stderr。
#
# 加新功能时不要改已经在用的部分。语音克隆、听写、抠图、下载各自有自己的
# 进程和依赖。为 AuK 升过一次全局 transformers（4.51 → 4.57），CosyVoice
# 的解码掩码对不上，生成的声音变成乱声。缺的包装在本子进程里解决，
# 不要为了这个新页去升级或改主环境里旧功能正在用的包和代码。
# ---------------------------------------------------------------------------

from __future__ import annotations

import json
import os
import sys
import traceback


def _prepare_env() -> None:
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["DESKASSIST_AUK_JOB"] = "1"
    os.environ.pop("QT_PLUGIN_PATH", None)
    os.environ.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _trim_ref_wav(src: str, dest: str, max_sec: float = 8.0) -> str:
    """配音只取参考音前几秒。整段很长时，后面的字容易被挤糊。"""
    try:
        import wave
        with wave.open(src, "rb") as w:
            rate = int(w.getframerate() or 0)
            frames = int(w.getnframes() or 0)
            if rate <= 0 or frames <= 0:
                return src
            if frames / float(rate) <= max_sec + 0.2:
                return src
            params = w.getparams()
            data = w.readframes(int(max_sec * rate))
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        with wave.open(dest, "wb") as out:
            out.setparams(params)
            out.writeframes(data)
        return dest if os.path.isfile(dest) else src
    except Exception:
        return src


def _emit(obj: dict) -> None:
    # ASCII JSON：中文走 \\uXXXX，避免 Windows 子进程标准流是 GBK 时把路径和报错写乱。
    sys.stdout.write(json.dumps(obj, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def _exc(err: BaseException) -> str:
    raw = str(err).strip()
    if raw:
        return f"{type(err).__name__}: {raw}"
    return f"{type(err).__name__}: {err!r}"


def _add_src(src: str) -> None:
    src = os.path.abspath(src or "")
    if src and src not in sys.path:
        sys.path.insert(0, src)


class _Engine:
    def __init__(self):
        self.engine = None
        self.key = None

    def load(self, req: dict):
        key = (
            req.get("config") or "",
            req.get("ckpt") or "",
            req.get("qwen") or "",
            bool(req.get("cpu_offload", True)),
        )
        if self.engine is not None and self.key == key:
            return
        _add_src(req.get("src") or "")
        from auk.infer.infer_auk import AukInfer

        self.engine = AukInfer(
            key[0],
            key[1],
            qwen_path=key[2] or None,
            cpu_offload=key[3],
        )
        self.key = key

    def run(self, req: dict) -> dict:
        instruction = (req.get("instruction") or "").strip()
        audio = (req.get("audio") or "").strip() or None
        out = (req.get("out") or "").strip()
        if not instruction:
            raise ValueError("没有指令")
        if not out:
            raise ValueError("没有输出路径")
        if audio and not os.path.isfile(audio):
            raise ValueError("找不到音频：" + audio)
        gen_text_early = (req.get("gen_text") or "").strip()
        trimmed = ""
        if gen_text_early and audio:
            trimmed_path = os.path.splitext(out)[0] + "_ref.wav"
            trimmed = _trim_ref_wav(audio, trimmed_path, 8.0)
            if trimmed and trimmed != audio and os.path.isfile(trimmed):
                audio = trimmed
            else:
                trimmed = ""
        from auk.infer.infer_auk import get_gen_duration, save_audio
        try:
            self.load(req)
            content = [{"type": "text", "text": instruction}]
            if audio:
                content.append({"type": "audio", "audio": audio})
            messages = [{"role": "user", "content": content}]
            seconds = req.get("gen_seconds")
            try:
                seconds = float(seconds) if seconds not in (None, "") else None
            except (TypeError, ValueError):
                seconds = None
            seconds = get_gen_duration(
                audio=audio,
                gen_text=(req.get("gen_text") or None),
                gen_seconds=seconds,
            )
            if seconds is not None and seconds > 28:
                raise ValueError("这段音频加上要生成的长度超过约 28 秒，请先裁短")
            wav, sr = self.engine.generate(messages, gen_seconds=seconds)
            save_audio(wav, sr, out)
        finally:
            if trimmed and os.path.isfile(trimmed):
                try:
                    os.remove(trimmed)
                except Exception:
                    pass
        return {"ok": True, "out": out.replace("\\", "/"), "seconds": seconds}


def _handle(engine: _Engine, req: dict) -> dict:
    try:
        if req.get("cmd") == "ping":
            return {"ok": True, "pong": True}
        return engine.run(req)
    except Exception as exc:
        return {"ok": False, "error": _exc(exc), "trace": traceback.format_exc()[-2000:]}


def serve() -> int:
    _prepare_env()
    engine = _Engine()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as exc:
            _emit({"ok": False, "error": _exc(exc)})
            continue
        if req.get("cmd") == "shutdown":
            _emit({"ok": True, "bye": True})
            return 0
        _emit(_handle(engine, req))
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--serve" in argv or not argv:
        return serve()
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
