# utils/ocr_job.py
# ---------------------------------------------------------------------------
# 无 Qt 的 OCR 子进程入口（PaddleOCR-VL-1.6）。
# 切勿在本文件 import PyQt5 —— 主进程 Qt 会与 Paddle / OpenCV 抢 DLL。
#
# 开发：python -m utils.ocr_job <meta.json>
# 打包：mainv916.exe --ocr-job <meta.json>
# meta.json:
#   { "action": "probe"|"ocr", "image": "可选 png", "model_dir": "...", "out": "out.json" }
# ---------------------------------------------------------------------------

from __future__ import annotations

import json
import os
import sys
import traceback


def _preload_torch_first() -> None:
    """paddleocr 的 import 链会经 paddlex → modelscope 拉入 torch。

    若先 import paddle 再 import torch，两者在进程里抢同名基础 DLL
    （libiomp5md / mklml 等），torch 加载 lib/shm.dll 时报
    WinError 127「找不到指定的程序」。实测先载 torch 再载 paddle 可避开，
    因此在任何 paddle 导入之前预载一次 torch。
    """
    try:
        import importlib.util

        if importlib.util.find_spec("torch") is not None:
            import torch  # noqa: F401
    except Exception:
        pass


def _prepare_env() -> None:
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    os.environ.setdefault("OPENCV_OPENCL_RUNTIME", "disabled")
    os.environ["DESKASSIST_OCR_JOB"] = "1"
    os.environ.pop("QT_PLUGIN_PATH", None)
    os.environ.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)
    os.environ.setdefault("FLAGS_allocator_strategy", "naive_best_fit")
    _preload_torch_first()


def _exc_text(err: BaseException) -> str:
    raw = str(err).strip()
    if raw:
        return f"{type(err).__name__}: {raw}"
    return f"{type(err).__name__}: {err!r}"


def _looks_like_vl_weights(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    st = os.path.join(path, "model.safetensors")
    cfg = os.path.join(path, "config.json")
    return os.path.isfile(st) and os.path.getsize(st) > 1024 * 1024 and os.path.isfile(cfg)


def _json_to_text(data) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data.strip()
    if isinstance(data, list):
        parts = [_json_to_text(x) for x in data]
        return "\n".join(p for p in parts if p)
    if not isinstance(data, dict):
        return str(data).strip()
    for k in ("res", "result", "data"):
        if k in data:
            inner = _json_to_text(data[k])
            if inner:
                return inner
    blocks = data.get("parsing_res_list")
    if isinstance(blocks, list):
        chunks = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            c = block.get("block_content") or block.get("content") or ""
            if c:
                chunks.append(str(c).strip())
        if chunks:
            return "\n".join(chunks)
    for k in ("markdown", "md", "rec_text", "text"):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _result_to_text(res) -> str:
    if res is None:
        return ""
    for attr in ("markdown", "str"):
        v = getattr(res, attr, None)
        if callable(v):
            try:
                v = v()
            except TypeError:
                pass
        if isinstance(v, str) and v.strip():
            return v.strip()
    data = getattr(res, "json", None)
    if callable(data):
        try:
            data = data()
        except TypeError:
            pass
    text = _json_to_text(data)
    if text:
        return text
    if isinstance(res, dict):
        return _json_to_text(res)
    try:
        return str(res).strip()
    except Exception:
        return ""


def _make_pipeline(model_dir: str):
    from paddleocr import PaddleOCRVL

    kwargs = {
        "pipeline_version": "v1.6",
        "vl_rec_model_dir": model_dir,
        "use_layout_detection": False,
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
    }
    try:
        import inspect
        params = inspect.signature(PaddleOCRVL.__init__).parameters
        kwargs = {k: v for k, v in kwargs.items() if k in params}
    except Exception:
        pass
    return PaddleOCRVL(**kwargs)


def _do_probe(meta: dict) -> dict:
    import importlib.util

    model_dir = (meta.get("model_dir") or "").strip()
    info = {
        "ok": False,
        "installed": False,
        "engine": "none",
        "model_dir": model_dir,
        "paddle": "",
        "paddleocr": False,
        "error": "",
    }
    has_weights = _looks_like_vl_weights(model_dir)
    info["has_weights"] = has_weights
    if not has_weights:
        info["error"] = "未找到 PaddleOCR-VL-1.6 权重（model/PaddleOCR-VL-1.6）"
        return info

    if importlib.util.find_spec("paddle") is None:
        info["error"] = "未安装 PaddlePaddle"
        return info
    if importlib.util.find_spec("paddleocr") is None:
        info["error"] = "未安装 paddleocr（需要 paddleocr[doc-parser]>=3.6.0）"
        return info

    try:
        import paddle
        info["paddle"] = str(getattr(paddle, "__version__", "") or "")
    except Exception as e:
        info["error"] = f"无法导入 paddle：{_exc_text(e)}"
        return info

    try:
        from paddleocr import PaddleOCRVL  # noqa: F401
        info["paddleocr"] = True
    except Exception as e:
        info["error"] = f"无法导入 PaddleOCRVL：{_exc_text(e)}"
        return info

    try:
        _make_pipeline(model_dir)
    except Exception as e:
        info["error"] = f"引擎初始化失败：{_exc_text(e)}"
        return info

    info.update({
        "ok": True,
        "installed": True,
        "engine": "paddleocr-vl",
        "label": "可使用 · PaddleOCR-VL-1.6",
        "detail": "本地权重已验证",
    })
    return info


def _predict_text(pipeline, image_path: str) -> str:
    output = pipeline.predict(image_path)
    texts = []
    for res in output or []:
        t = _result_to_text(res)
        if t:
            texts.append(t)
    return "\n\n".join(texts).strip() or "（未识别到文字）"


def _do_ocr(image_path: str, model_dir: str) -> dict:
    if not image_path or not os.path.isfile(image_path):
        return {"ok": False, "engine": "none", "error": "无有效图像"}
    if not _looks_like_vl_weights(model_dir):
        return {
            "ok": False,
            "engine": "none",
            "error": "未找到 PaddleOCR-VL-1.6 权重",
        }
    pipeline = _make_pipeline(model_dir)
    return {"ok": True, "engine": "paddleocr-vl", "text": _predict_text(pipeline, image_path)}


def _do_ocr_keepalive(image_path: str, model_dir: str, cache: dict) -> dict:
    """常驻模式：管线只建一次，之后的请求复用，避免每次冷启动加载权重。"""
    if not image_path or not os.path.isfile(image_path):
        return {"ok": False, "engine": "none", "error": "无有效图像"}
    if not _looks_like_vl_weights(model_dir):
        return {
            "ok": False,
            "engine": "none",
            "error": "未找到 PaddleOCR-VL-1.6 权重",
        }
    key = os.path.normcase(os.path.abspath(model_dir))
    pipeline = cache.get(key)
    if pipeline is None:
        pipeline = _make_pipeline(model_dir)
        cache[key] = pipeline
    return {"ok": True, "engine": "paddleocr-vl", "text": _predict_text(pipeline, image_path)}


def server_main() -> int:
    """常驻 OCR 服务：从 stdin 逐行读 meta.json 路径，处理后把结果写进 meta['out']。

    管线（含 1.9GB 权重）只加载一次，之后每个截图请求都复用，避免逐次冷启动。
    父进程关闭 stdin 即退出。
    """
    _prepare_env()
    cache: dict = {}
    while True:
        line = sys.stdin.readline()
        if not line:
            return 0
        meta_path = line.strip()
        if not meta_path:
            continue
        out_path = ""
        try:
            with open(meta_path, encoding="utf-8-sig") as f:
                meta = json.load(f)
            if not isinstance(meta, dict):
                raise ValueError("meta.json 不是对象")
            out_path = meta.get("out") or ""
            action = (meta.get("action") or "ocr").strip().lower()
            model_dir = (meta.get("model_dir") or "").strip()
            if action == "probe":
                payload = _do_probe(meta)
            elif action == "ocr":
                payload = _do_ocr_keepalive(meta.get("image") or "", model_dir, cache)
            else:
                payload = {"ok": False, "error": f"未知 action: {action}"}
            if out_path:
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False)
        except Exception:
            if out_path:
                try:
                    with open(out_path, "w", encoding="utf-8") as f:
                        json.dump(
                            {"ok": False, "error": traceback.format_exc()[-2000:]},
                            f,
                            ensure_ascii=False,
                        )
                except Exception:
                    pass


def run_from_meta(meta_path: str) -> int:
    _prepare_env()
    err_path = None
    out_path = None
    try:
        with open(meta_path, encoding="utf-8-sig") as f:
            meta = json.load(f)
        if not isinstance(meta, dict):
            raise ValueError("meta.json 不是对象")

        out_path = meta.get("out")
        if not out_path:
            raise ValueError("meta.json 缺少 out")
        err_path = os.path.join(os.path.dirname(out_path) or ".", "err.txt")

        action = (meta.get("action") or "ocr").strip().lower()
        model_dir = (meta.get("model_dir") or "").strip()
        if action == "probe":
            payload = _do_probe(meta)
        elif action == "ocr":
            payload = _do_ocr(meta.get("image") or "", model_dir)
        else:
            payload = {"ok": False, "error": f"未知 action: {action}"}

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        return 0 if payload.get("ok") else 1
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
        if out_path:
            try:
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(
                        {"ok": False, "error": err[-2000:]},
                        f,
                        ensure_ascii=False,
                    )
            except Exception:
                pass
        return 1


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if argv and str(argv[0]).strip().lower() in ("--server", "-s"):
        return server_main()
    if not argv or not str(argv[0]).endswith(".json"):
        print("usage: python -m utils.ocr_job <meta.json> | --server", file=sys.stderr)
        return 2
    return run_from_meta(argv[0])


if __name__ == "__main__":
    raise SystemExit(main())
