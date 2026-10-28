# -*- coding: utf-8 -*-
# utils/translate_job.py
# ---------------------------------------------------------------------------
# 无 Qt 的 opus-MT 翻译子进程入口（勿 import PyQt5）。
#
# 单次：
#   python -m utils.translate_job <meta.json>
# 常驻：
#   python -m utils.translate_job --server
#   stdin 每行一个 meta.json 路径；写完 out 后继续等。
#
# meta.json:
#   {
#     "direction": "zh-en" | "en-zh",
#     "texts": ["...", ...],
#     "out": "out.json",
#     "force_cpu": false
#   }
# out.json:
#   { "ok": true, "texts": ["...", ...], "device": "cuda"|"cpu" }
#   或 { "ok": false, "error": "..." }
# ---------------------------------------------------------------------------
from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Dict, List, Optional, Tuple
import re

_MODEL_CACHE: Dict[str, Tuple[object, object, str]] = {}


def _base_dir() -> str:
    try:
        from utils.app_paths import app_root
        return app_root()
    except Exception:
        if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
            return os.path.dirname(os.path.abspath(sys.executable))
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _model_dir(direction: str) -> str:
    folder = "opus-mt-zh-en" if direction == "zh-en" else "opus-mt-en-zh"
    beside = os.path.join(_base_dir(), "model", folder)
    if os.path.isdir(beside):
        return beside
    try:
        from utils.app_paths import resource_path
        bundled = resource_path("model", folder)
        if os.path.isdir(bundled):
            return bundled
    except Exception:
        pass
    return beside


def _looks_ready(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    if not os.path.isfile(os.path.join(path, "config.json")):
        return False
    for name in ("pytorch_model.bin", "model.safetensors"):
        if os.path.isfile(os.path.join(path, name)):
            return True
    try:
        for fn in os.listdir(path):
            fp = os.path.join(path, fn)
            if (
                os.path.isfile(fp)
                and fn.endswith((".bin", ".safetensors"))
                and os.path.getsize(fp) > 1_000_000
            ):
                return True
    except OSError:
        pass
    return False


def _ensure_sacremoses() -> None:
    try:
        import sacremoses  # noqa: F401
    except ImportError:
        import subprocess
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", "sacremoses"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def _load_pair(direction: str, force_cpu: bool):
    key = f"{direction}|{'cpu' if force_cpu else 'auto'}"
    hit = _MODEL_CACHE.get(key)
    if hit is not None:
        return hit

    _ensure_sacremoses()
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    path = _model_dir(direction)
    if not _looks_ready(path):
        raise FileNotFoundError(f"缺少翻译模型目录：{path}")

    tok = AutoTokenizer.from_pretrained(path, local_files_only=True)
    model = AutoModelForSeq2SeqLM.from_pretrained(path, local_files_only=True)
    device = "cpu"
    if not force_cpu and torch.cuda.is_available():
        try:
            model.to("cuda")
            device = "cuda"
        except Exception:
            model.to("cpu")
            device = "cpu"
    else:
        model.to("cpu")
    model.eval()
    _MODEL_CACHE[key] = (tok, model, device)
    return _MODEL_CACHE[key]


def _mostly_latin(text: str) -> bool:
    """已是英文/拉丁文主导 → 不送 zh-en，避免 Marian 胡编 I'm sorry 循环。"""
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 3:
        return False
    latin = sum(1 for c in letters if ord(c) < 128)
    return (latin / max(1, len(letters))) >= 0.85


def _han_ratio(text: str) -> float:
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    han = sum(1 for c in chars if "\u4e00" <= c <= "\u9fff")
    return han / len(chars)


def _mostly_punct(text: str) -> bool:
    core = "".join(ch for ch in text if not ch.isspace())
    if not core:
        return True
    return sum(1 for ch in core if ch.isalnum() or ("\u4e00" <= ch <= "\u9fff")) == 0



def _post_zh_en_glossary(text: str) -> str:
    """服饰/提示词域：修正 opus-MT zh-en 常见错译（不改源文）。"""
    if not text:
        return text
    out = text
    # 长短语优先；大小写不敏感整词/常见连写
    pairs = [
        (r"\bhigh[- ]coloured\b", "high-collar"),
        (r"\bhigh[- ]colored\b", "high-collar"),
        (r"\bhigh[- ]circle\b", "high-collar"),
        (r"\bhigh[- ]capitulation\b", "high-collar"),
        (r"\bhigh[- ]caps?\b", "high-collar"),
        (r"\bmedical\s+cliques?\b", "medical bodysuit"),
        (r"\bmedical\s+combs?\b", "medical bodysuit"),
        (r"\bmedical\s+hygien\.?\b", "medical bodysuit"),
        (r"\bmedical\s+hygienic\b", "medical bodysuit"),
        (r"\bcliques?\b", "bodysuit"),
        (r"\bknickers\b", "cuffs"),
        (r"\bammonium\b", "spandex"),
        (r"\bammonia\b", "spandex"),
        (r"\bchip\s+clutters?\b", "panel insets"),
        (r"\bchip\s+suits?\b", "panel suits"),
        (r"\bNavy\s+blue\s+chip\b", "navy blue panel"),
        (r"\bnavy\s+blue\s+chip\b", "navy blue panel"),
        (r"\bblue\s+chip\b", "blue panel"),
        (r"\bwhite[- ]collar\s+subjects?\b", "white main body"),
        (r"\bfront\s+flaps?\b", "front panels"),
        (r"\bformer\s+flaps?\b", "front panels"),
        (r"\bchest\s+white\b", "white front"),
        (r"\bnet\s+eyes\b", "mesh vents"),
        (r"\bballistics\b", "stretch fit"),
        (r"\bdumb[- ]medic\b", "matte medical"),
        (r"\bdummies\b", "matte fabric"),
        (r"\bmute\s+ammonium\b", "matte spandex"),
        (r"\bmute\s+ammonia\b", "matte spandex"),
        (r"\bmute\s+spandex\b", "matte spandex"),
        # 单独 chip（服饰镶片语境）放后面，避免误伤非服饰时仍可接受 panel
        (r"\bchips\b", "panels"),
        (r"\bchip\b", "panel"),
    ]
    for pat, repl in pairs:
        out = re.sub(pat, repl, out, flags=re.IGNORECASE)
    return out


def _degenerate_translation(src: str, out: str) -> bool:
    """检测重复退化 / 道歉循环等垃圾输出。

    注意：中文→英文字符常膨胀到数倍；服装译文又大量出现 collar/coat/combat
    等 "co*" 词。旧逻辑用含空白的 3-gram（如 " co"）且 len>src*4，会把
    正常服装/场景英文误判为退化，导致左栏一直显示中文原文。
    """
    if not out:
        return True
    o = out.strip()
    # 中文密、英文疏：短句英译常可到 5–8× 字符；仅拦极端超长
    if len(o) > max(240, len(src) * 10):
        return True
    low = o.lower()
    for phrase in ("i'm sorry", "i am sorry", "i don't know", "i do not know"):
        if low.count(phrase) >= 2:
            return True
    # 字符 n-gram：跳过含空白片段（" co"/" th"/" an" 在正常英文极常见）
    for n in (3, 4, 5, 8):
        if len(o) < n * 5:
            continue
        thr = 10 if n <= 3 else 8
        for i in range(0, min(48, len(o) - n)):
            gram = o[i : i + n]
            if any(ch.isspace() for ch in gram):
                continue
            if n <= 3 and not gram.isalnum():
                continue
            if sum(1 for ch in gram if ch.isalnum()) < max(2, n - 2):
                continue
            if o.count(gram) >= thr:
                return True
    words = [w for w in low.split() if any(c.isalpha() for c in w) and len(w) >= 2]
    if len(words) >= 8:
        from collections import Counter
        common = Counter(words).most_common(1)
        if common and common[0][1] >= max(6, len(words) // 3):
            return True
    return False


def _should_skip(text: str, direction: str) -> bool:
    if not text or _mostly_punct(text):
        return True
    if direction == "zh-en":
        # 已是英文主导 → 不送中→英
        return _mostly_latin(text)
    if direction == "en-zh":
        # 中文段保持；非拉丁主导 / 英文太短 → 不送英→中
        if _han_ratio(text) >= 0.5:
            return True
        if not _mostly_latin(text):
            return True
        latin_letters = sum(1 for c in text if c.isalpha() and ord(c) < 128)
        return latin_letters < 8
    return False


def _translate_texts(
    texts: List[str], direction: str, force_cpu: bool
) -> Tuple[List[str], str]:
    import torch

    cleaned = [str(x or "").strip() for x in texts]
    if not cleaned:
        return [], "cpu"

    result = list(cleaned)
    todo_idx = [i for i, t in enumerate(cleaned) if not _should_skip(t, direction)]
    if not todo_idx:
        return result, "cpu"

    tok, model, device = _load_pair(direction, force_cpu)
    # 逐条推理：短句/英文混批时 beam+padding 易崩成 I'm sorry 循环
    for i in todo_idx:
        one = cleaned[i]
        enc = tok(
            [one],
            return_tensors="pt",
            padding=False,
            truncation=True,
            max_length=512,
        )
        enc = {k: v.to(device) for k, v in enc.items()}
        with torch.no_grad():
            # 服饰等长句 96 易截断→退化；按源长放宽
            max_new = 96
            if len(one) > 80:
                max_new = 160
            if len(one) > 160:
                max_new = 220
            gen = model.generate(
                **enc,
                max_new_tokens=max_new,
                num_beams=4,
                no_repeat_ngram_size=3,
                repetition_penalty=1.25,
                early_stopping=True,
            )
        out = tok.decode(gen[0], skip_special_tokens=True).strip()
        if not out or _degenerate_translation(one, out):
            # 失败时返回空串（勿回传原文），避免上层把中文缓存成「英文译文」
            result[i] = ""
        else:
            if direction == "zh-en":
                out = _post_zh_en_glossary(out)
                # 译文仍大量汉字 → 视为失败
                if _han_ratio(out) >= 0.18:
                    result[i] = ""
                else:
                    result[i] = out
            else:
                result[i] = out
    return result, device


def _handle_meta(meta: dict) -> dict:
    direction = str(meta.get("direction") or "zh-en").strip().lower()
    if direction not in ("zh-en", "en-zh"):
        direction = "zh-en"
    texts = meta.get("texts")
    if not isinstance(texts, list):
        raise ValueError("texts 必须是字符串列表")
    force_cpu = bool(meta.get("force_cpu", False))
    out_texts, device = _translate_texts([str(x) for x in texts], direction, force_cpu)
    return {"ok": True, "texts": out_texts, "device": device, "direction": direction}


def _write_out(out_path: str, payload: dict) -> None:
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, out_path)


def run_meta(meta_path: str) -> int:
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    out_path = None
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        out_path = meta.get("out")
        if not out_path:
            raise ValueError("meta 缺少 out")
        payload = _handle_meta(meta)
        _write_out(out_path, payload)
        return 0
    except Exception as e:
        err = traceback.format_exc()
        try:
            sys.stderr.write(err)
        except Exception:
            pass
        if out_path:
            try:
                _write_out(out_path, {"ok": False, "error": str(e), "trace": err[-2000:]})
            except Exception:
                pass
        return 1


def serve() -> int:
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    # 预热提示到 stderr，不影响协议
    try:
        sys.stderr.write("translate_job server ready\n")
        sys.stderr.flush()
    except Exception:
        pass
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        meta_path = line.strip()
        if not meta_path:
            continue
        if meta_path in ("quit", "exit"):
            break
        run_meta(meta_path)
        try:
            sys.stdout.write("ok\n")
            sys.stdout.flush()
        except Exception:
            pass
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("--server", "-s"):
        return serve()
    if not argv:
        sys.stderr.write("usage: python -m utils.translate_job <meta.json>|--server\n")
        return 2
    return run_meta(argv[0])


if __name__ == "__main__":
    raise SystemExit(main())
