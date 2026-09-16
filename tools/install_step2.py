# -*- coding: utf-8 -*-
"""Step 2: download model weights into model/."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def _fix_stdio():
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def pause(msg: str = "按任意键继续..."):
    try:
        input(msg)
    except EOFError:
        pass


def main() -> int:
    _fix_stdio()
    os.chdir(ROOT)
    print("=" * 60)
    print("2/3 安装模型 写入 model/")
    print("桌面助手 v9.16")
    print("=" * 60)
    print()
    print("对应旧流程：2安装-模型依赖.bat 里的「下载权重」部分。")
    print("模型相关 pip 已在第 1 步通过 deps / requirements-models 安装。")
    print()
    print("model/ 下均为开源内容，下载清单：")
    print("  [1] paddleocr      PaddleOCR-VL-1.6      约 1.9GB  截图OCR")
    print("  [2] whisper-tiny   faster-whisper-tiny           语音转写小")
    print("  [3] whisper-small  faster-whisper-small          语音转写推荐")
    print("  [4] whisper-large  faster-whisper-large-v3       语音转写大")
    print("  [5] cosyvoice      Fun-CosyVoice3-0.5B   约 9GB   语音克隆")
    print()
    print("默认会交互选择；也可直接：")
    print("  python tools/download_models.py --list")
    print("  python tools/download_models.py --only paddleocr --mirror modelscope")
    print("-" * 60)
    pause("按回车开始下载菜单...")
    rc = subprocess.call([PY, str(ROOT / "tools" / "download_models.py")])
    print()
    if rc != 0:
        print("[警告] 下载脚本退出码=", rc)
    else:
        print("[成功] 第 2 步结束。下一步可双击 3启动-程序.bat")
    return rc


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消。")
        raise SystemExit(130)
