# -*- coding: utf-8 -*-
"""复刻 ocr_job.py / cosyvoice_job.py 里真实的导入顺序，打印完整堆栈。

体检工具（setup_components.py --check）测的是"干净环境下单独 import"，
跟 app 子进程的真实导入顺序不一样——尤其是 OCR 子进程会故意先 import 一次
torch 再 import paddle（见 utils/ocr_job.py 里 _preload_torch_first 的注释），
这个顺序在某些 torch/paddle 版本组合下反而会互相顶掉 DLL。
这个脚本按 app 实际顺序复现，失败时打印完整 traceback（app 本身只截获了
异常的 repr()，信息不够）。

用法：
  python tools\\diagnose_dll.py ocr
  python tools\\diagnose_dll.py cosyvoice
  python tools\\diagnose_dll.py both
"""
from __future__ import annotations

import importlib.util
import os
import sys
import traceback
from pathlib import Path


def _find_root() -> Path:
    here = Path(__file__).resolve()
    for cand in (here.parent, *here.parents):
        if (cand / "mainv916.py").is_file():
            return cand
    return here.parents[1] if len(here.parents) > 1 else here.parent


ROOT = _find_root()
MODEL = ROOT / "model"


def hr(t: str):
    print("\n" + "=" * 60)
    print(t)
    print("=" * 60)


def diag_ocr():
    hr("OCR：复刻 _preload_torch_first() → import paddle 的真实顺序")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

    print("① 先 import torch（app 会这么做，见 ocr_job.py 注释）")
    torch_ok = importlib.util.find_spec("torch") is not None
    if torch_ok:
        try:
            import torch  # noqa: F401
            print(f"   torch 导入成功：{torch.__version__}，"
                  f"CUDA 可用={torch.cuda.is_available()}")
        except Exception:
            print("   torch 导入失败：")
            traceback.print_exc()
    else:
        print("   没装 torch，跳过（app 里这一步也会跳过）")

    print("\n② 再 import paddle（这一步就是截图报错的地方）")
    try:
        import paddle
        print(f"   paddle 导入成功：{getattr(paddle, '__version__', '?')}")
    except Exception:
        print("   paddle 导入失败，完整堆栈：")
        traceback.print_exc()
        print("\n   如果①成功②失败：基本确认是 torch/paddle 的 cudnn DLL 撞名。")
        print("   最直接的解法：把 paddle 换成 CPU 版，彻底绕开这个 DLL：")
        print("     python tools\\setup_components.py --only paddle-cpu")
        return

    print("\n③ 试一次真正的 PaddleOCR-VL 初始化")
    try:
        from paddleocr import PaddleOCRVL
        model_dir = str(MODEL / "PaddleOCR-VL-1.6")
        PaddleOCRVL(
            pipeline_version="v1.6",
            vl_rec_model_dir=model_dir,
            use_layout_detection=False,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
        )
        print("   初始化成功")
    except Exception:
        print("   初始化失败，完整堆栈：")
        traceback.print_exc()


def diag_cosyvoice():
    hr("CosyVoice：复刻 _import_automodel() 的真实顺序")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

    home = MODEL / "CosyVoice"
    matcha = home / "third_party" / "Matcha-TTS"
    for p in (str(home), str(matcha)):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)
    print(f"CosyVoice 源码目录：{home}（存在={home.is_dir()}）")
    print(f"Matcha-TTS 子模块：{matcha}（存在={matcha.is_dir()}）")

    print("\n① import torch")
    try:
        import torch
        print(f"   成功：{torch.__version__}，CUDA 可用={torch.cuda.is_available()}")
    except Exception:
        print("   失败，完整堆栈：")
        traceback.print_exc()
        return

    print("\n② from cosyvoice.cli.cosyvoice import AutoModel（app 报的空 ImportError 就是这里）")
    try:
        from cosyvoice.cli.cosyvoice import AutoModel  # noqa: F401
        print("   成功（AutoModel）")
        return
    except Exception:
        print("   AutoModel 导入失败，完整堆栈：")
        traceback.print_exc()

    print("\n③ 退回 from cosyvoice.cli.cosyvoice import CosyVoice")
    try:
        from cosyvoice.cli.cosyvoice import CosyVoice  # noqa: F401
        print("   成功（CosyVoice）")
    except Exception:
        print("   同样失败，完整堆栈：")
        traceback.print_exc()


def main():
    which = (sys.argv[1] if len(sys.argv) > 1 else "both").strip().lower()
    if which in ("ocr", "both"):
        diag_ocr()
    if which in ("cosyvoice", "both"):
        diag_cosyvoice()


if __name__ == "__main__":
    main()
