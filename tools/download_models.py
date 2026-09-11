# -*- coding: utf-8 -*-
"""v9.16 外置模型下载 → model/

  python tools/download_models.py
  python tools/download_models.py --only paddleocr
  python tools/download_models.py --only whisper-small
  python tools/download_models.py --only cosyvoice --mirror modelscope
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
}

def _ready(name: str) -> bool:
    d = MODEL / name
    if not d.is_dir():
        return False
    if name == "CosyVoice":
        return (d / "cosyvoice").is_dir() or (d / "README.md").is_file()
    for f in d.rglob("*"):
        if f.is_file() and f.stat().st_size > 1_000_000 and ".git" not in f.parts:
            return True
    return False

def _hf(repo, dest: Path):
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-U", "huggingface_hub"])
        from huggingface_hub import snapshot_download
    dest.parent.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=repo, local_dir=str(dest))

def _ms(repo, dest: Path):
    try:
        from modelscope import snapshot_download
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-U", "modelscope"])
        from modelscope import snapshot_download
    dest.parent.mkdir(parents=True, exist_ok=True)
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
    if mirror == "modelscope":
        try:
            _ms(item["ms"], dest); return
        except Exception as e:
            print("ModelScope 失败:", e)
    _hf(item["hf"], dest)

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
