# utils/matting_job.py
# ---------------------------------------------------------------------------
# 无 Qt 的抠图子进程（BiRefNet-general ONNX）。
# 切勿在本文件 import PyQt5 —— 主进程 Qt 会与 onnxruntime 抢 DLL。
#
# 开发：python -m utils.matting_job <meta.json>
# 打包：mainv917.exe --matting-job <meta.json>
# meta.json:
#   { "image": "输入图", "out": "输出 png", "model": "可选 onnx 路径" }
# 进度：stdout 打印 PCT 0–100
# ---------------------------------------------------------------------------

from __future__ import annotations

import json
import os
import sys
import traceback


_INPUT_SIZE = 1024
_IMAGENET_MEAN = (0.485, 0.456, 0.406)
_IMAGENET_STD = (0.229, 0.224, 0.225)


def _prepare_env() -> None:
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    os.environ["DESKASSIST_MATTING_JOB"] = "1"
    os.environ.pop("QT_PLUGIN_PATH", None)
    os.environ.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)


def _pct(n: int) -> None:
    try:
        n = max(0, min(100, int(n)))
    except (TypeError, ValueError):
        n = 0
    sys.stdout.write("PCT %s\n" % n)
    sys.stdout.flush()


def _fail(msg: str, code: int = 2) -> int:
    sys.stderr.write(msg.rstrip() + "\n")
    sys.stderr.flush()
    return code


def find_birefnet_onnx(explicit: str = "") -> str:
    """优先 meta 指定路径，其次 model/BiRefNet-general 下较大的 .onnx。"""
    p = (explicit or "").strip()
    if p and os.path.isfile(p) and os.path.getsize(p) > 1_000_000:
        return p
    roots = []
    try:
        from utils.app_paths import model_file
        roots.append(model_file("BiRefNet-general"))
    except Exception:
        pass
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    roots.append(os.path.join(here, "model", "BiRefNet-general"))
    if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
        roots.append(os.path.join(
            os.path.dirname(os.path.abspath(sys.executable)),
            "model", "BiRefNet-general",
        ))
    seen = set()
    for d in roots:
        if not d:
            continue
        key = os.path.normcase(os.path.abspath(d))
        if key in seen:
            continue
        seen.add(key)
        if os.path.isfile(d) and d.lower().endswith(".onnx") and os.path.getsize(d) > 10_000_000:
            return d
        if not os.path.isdir(d):
            continue
        best = ""
        best_sz = 0
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in names:
            if not name.lower().endswith(".onnx"):
                continue
            fp = os.path.join(d, name)
            try:
                sz = os.path.getsize(fp)
            except OSError:
                continue
            if sz > best_sz:
                best_sz = sz
                best = fp
        if best and best_sz > 10_000_000:
            return best
    return ""


def _open_rgb(path: str):
    from PIL import Image, ImageOps

    try:
        Image.MAX_IMAGE_PIXELS = None
    except Exception:
        pass
    img = Image.open(path)
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    rgba = img.convert("RGBA")
    if img is not rgba:
        try:
            img.close()
        except Exception:
            pass
    return rgba


def _preprocess(rgba) -> "object":
    import numpy as np
    from PIL import Image as PILImage

    rgb = rgba.convert("RGB").resize((_INPUT_SIZE, _INPUT_SIZE), PILImage.BILINEAR)
    arr = np.asarray(rgb, dtype=np.float32) / 255.0
    try:
        rgb.close()
    except Exception:
        pass
    mean = np.array(_IMAGENET_MEAN, dtype=np.float32).reshape((1, 1, 3))
    std = np.array(_IMAGENET_STD, dtype=np.float32).reshape((1, 1, 3))
    arr = (arr - mean) / std
    arr = np.transpose(arr, (2, 0, 1))[None, ...]
    return np.ascontiguousarray(arr)


def _postprocess_mask(raw, orig_w: int, orig_h: int):
    import numpy as np
    from PIL import Image as PILImage

    mask = np.asarray(raw)
    while mask.ndim > 2:
        mask = mask[0]
    mask = mask.astype(np.float32)
    mn, mx = float(mask.min()), float(mask.max())
    if mx > 1.5 or mn < -0.1:
        mask = 1.0 / (1.0 + np.exp(-np.clip(mask, -40.0, 40.0)))
    else:
        mask = np.clip(mask, 0.0, 1.0)
    im = PILImage.fromarray((mask * 255.0).clip(0, 255).astype("uint8"), "L")
    if im.size != (orig_w, orig_h):
        im = im.resize((orig_w, orig_h), PILImage.BILINEAR)
    return im


def _cuda_ep_usable() -> bool:
    """包里有 CUDA EP 不等于本机能加载（常见 WinError 126 / CUDA 版本不对）。"""
    try:
        import ctypes
        import onnxruntime as ort
        dll = os.path.join(os.path.dirname(ort.__file__), "capi", "onnxruntime_providers_cuda.dll")
        if not os.path.isfile(dll):
            return False
        ctypes.WinDLL(dll)
        return True
    except Exception:
        return False


def _make_session(model_path: str):
    import onnxruntime as ort

    try:
        ort.set_default_logger_severity(3)
    except Exception:
        pass
    so = ort.SessionOptions()
    so.intra_op_num_threads = 2
    so.inter_op_num_threads = 1
    so.log_severity_level = 3
    avail = list(ort.get_available_providers() or [])

    def _open(providers):
        return ort.InferenceSession(model_path, sess_options=so, providers=providers)

    # get_available_providers 只说明 DLL 在包里，不代表本机 CUDA/cuDNN 能加载。
    # 混用时 ORT 会打星号横幅并抛错，不会自动落到 CPU。
    if "CUDAExecutionProvider" in avail and _cuda_ep_usable():
        try:
            return _open(["CUDAExecutionProvider", "CPUExecutionProvider"])
        except Exception as e:
            sys.stderr.write("CUDA 不可用，改用 CPU：%s\n" % e)
            sys.stderr.flush()
    if "DmlExecutionProvider" in avail:
        try:
            return _open(["DmlExecutionProvider", "CPUExecutionProvider"])
        except Exception as e:
            sys.stderr.write("DirectML 不可用，改用 CPU：%s\n" % e)
            sys.stderr.flush()
    return _open(["CPUExecutionProvider"])


def run_job(meta: dict) -> int:
    image = str(meta.get("image") or "").strip()
    out = str(meta.get("out") or "").strip()
    model = str(meta.get("model") or "").strip()
    if not image or not os.path.isfile(image):
        return _fail("找不到输入图")
    if not out:
        return _fail("未指定输出路径")
    model = find_birefnet_onnx(model)
    if not model:
        return _fail("找不到 BiRefNet-general 模型（请放到 model/BiRefNet-general/*.onnx）")

    _pct(5)
    try:
        import numpy as np  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception as e:
        return _fail("缺少 Pillow / numpy：%s" % e)
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        return _fail("缺少 onnxruntime，请 pip install onnxruntime")

    _pct(8)
    try:
        sess = _make_session(model)
    except Exception as e:
        return _fail("加载 ONNX 失败：%s: %s" % (type(e).__name__, e))

    _pct(25)
    rgba = _open_rgb(image)
    ow, oh = rgba.size
    inp = _preprocess(rgba)
    _pct(40)

    in_name = sess.get_inputs()[0].name
    try:
        raw = sess.run(None, {in_name: inp})[0]
    except Exception as e:
        return _fail("推理失败：%s: %s" % (type(e).__name__, e))
    _pct(85)

    alpha = _postprocess_mask(raw, ow, oh)
    rgba.putalpha(alpha)
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    rgba.save(out, format="PNG", optimize=True)
    try:
        alpha.close()
        rgba.close()
    except Exception:
        pass
    if not os.path.isfile(out) or os.path.getsize(out) < 32:
        return _fail("写出失败")
    _pct(100)
    return 0


def main(argv=None) -> int:
    _prepare_env()
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        sys.stderr.write("usage: python -m utils.matting_job <meta.json>\n")
        return 2
    meta_path = args[0]
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except Exception as e:
        return _fail("无法读取 meta：%s" % e)
    if not isinstance(meta, dict):
        return _fail("meta 不是对象")
    try:
        return run_job(meta)
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
