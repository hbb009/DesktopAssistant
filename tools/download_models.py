# -*- coding: utf-8 -*-
"""v9.19 外置模型下载 → model/

  python tools/download_models.py
  python tools/download_models.py --only paddleocr
  python tools/download_models.py --only whisper-small
  python tools/download_models.py --only cosyvoice --mirror modelscope
  python tools/download_models.py --only hy-mt2
"""
from __future__ import annotations
import argparse, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "model"

CATALOG = {
    "paddleocr": {"title": "PaddleOCR-VL-1.6（约 1.9GB）", "local": "PaddleOCR-VL-1.6",
                  "hf": "PaddlePaddle/PaddleOCR-VL-1.6", "ms": "PaddlePaddle/PaddleOCR-VL-1.6", "kind": "snapshot"},
    "whisper-tiny": {"title": "faster-whisper-tiny", "local": "faster-whisper-tiny",
                     "hf": "Systran/faster-whisper-tiny", "ms": "pengzhendong/faster-whisper-tiny", "kind": "snapshot"},
    "whisper-small": {"title": "faster-whisper-small（推荐起步）", "local": "faster-whisper-small",
                      "hf": "Systran/faster-whisper-small", "ms": "pengzhendong/faster-whisper-small", "kind": "snapshot"},
    "whisper-large": {"title": "faster-whisper-large-v3", "local": "faster-whisper-large-v3",
                      "hf": "Systran/faster-whisper-large-v3", "ms": "pengzhendong/faster-whisper-large-v3", "kind": "snapshot"},
    "cosyvoice": {"title": "Fun-CosyVoice3-0.5B（约 9GB）", "local": "Fun-CosyVoice3-0.5B",
                  "hf": "FunAudioLLM/Fun-CosyVoice3-0.5B-2512", "ms": "FunAudioLLM/Fun-CosyVoice3-0.5B-2512", "kind": "snapshot"},
    "cosyvoice-code": {"title": "CosyVoice 源码", "local": "CosyVoice", "kind": "git",
                       "url": "https://github.com/FunAudioLLM/CosyVoice.git"},
    "hy-mt2": {"title": "Hy-MT2-1.8B（腾讯混元翻译，约 4GB）", "local": "Hy-MT2-1.8B",
              "hf": "tencent/Hy-MT2-1.8B", "ms": "Tencent-Hunyuan/Hy-MT2-1.8B", "kind": "snapshot",
              "allow": ["*.json", "*.jinja", "*.txt", "*.model", "*.safetensors", "tokenizer*"]},
}

def _ready(name: str) -> bool:
    d = MODEL / name
    if not d.is_dir():
        return False
    if name == "CosyVoice":
        return (d / "cosyvoice").is_dir() or (d / "README.md").is_file()
    if name == "Hy-MT2-1.8B":
        # tokenizer.json 约 9MB，不能当成权重。完整 model.safetensors 为 4077072784 字节。
        w = d / "model.safetensors"
        return w.is_file() and w.stat().st_size >= 3_000_000_000
    for f in d.rglob("*"):
        if f.is_file() and f.stat().st_size > 1_000_000 and ".git" not in f.parts:
            return True
    return False

def _hf(repo, dest: Path, allow_patterns=None):
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-U", "huggingface_hub"])
        from huggingface_hub import snapshot_download
    dest.parent.mkdir(parents=True, exist_ok=True)
    kw = {}
    if allow_patterns:
        kw["allow_patterns"] = allow_patterns
    snapshot_download(repo_id=repo, local_dir=str(dest), **kw)

def _ms(repo, dest: Path, allow_patterns=None):
    try:
        from modelscope import snapshot_download
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-U", "modelscope"])
        from modelscope import snapshot_download
    dest.parent.mkdir(parents=True, exist_ok=True)
    kw = {}
    # ModelScope 部分版本支持 allow_patterns；不支持时整库下完再靠本机裁剪
    if allow_patterns:
        kw["allow_patterns"] = allow_patterns
    try:
        snapshot_download(repo, local_dir=str(dest), **kw)
    except TypeError:
        snapshot_download(repo, local_dir=str(dest))

def _git(url, dest: Path):
    if dest.exists():
        print("已存在", dest); return
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(["git", "clone", "--recursive", url, str(dest)])

def download(key, mirror):
    item = CATALOG[key]
    dest = MODEL / item["local"]
    if _ready(item["local"]):
        print("[跳过]", item["title"]); return
    print("===", item["title"], "===")
    if item["kind"] == "git":
        _git(item["url"], dest); return
    allow = item.get("allow")
    if mirror == "modelscope":
        try:
            _ms(item["ms"], dest, allow_patterns=allow); return
        except Exception as e:
            print("ModelScope failed:", e)
    _hf(item["hf"], dest, allow_patterns=allow)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--mirror", choices=["hf", "modelscope"], default="hf")
    ap.add_argument("--all-light", action="store_true")
    args = ap.parse_args()
    if args.list:
        for k, v in CATALOG.items():
            print(f"  [{'OK' if _ready(v['local']) else '--'}] {k:16} {v['title']}")
        return 0
    keys = list(args.only)
    if args.all_light:
        keys += ["paddleocr", "whisper-small"]
    if not keys:
        for i, (k, v) in enumerate(CATALOG.items(), 1):
            print(f"  {i}. {k} — {v['title']}")
        print("  a. 轻量（OCR+whisper-small）  q. 退出")
        c = input("选择：").strip().lower()
        if c in {"", "q"}: return 0
        if c == "a": keys = ["paddleocr", "whisper-small"]
        elif c.isdigit(): keys = [list(CATALOG)[int(c) - 1]]
        else: keys = [c]
    for k in keys:
        if k not in CATALOG:
            print("未知", k); return 2
        download(k, args.mirror)
    print("完成", MODEL)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
