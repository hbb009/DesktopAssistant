# -*- coding: utf-8 -*-
"""Step 3: optional health check then launch mainv916.py."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
MAIN = ROOT / "mainv916.py"


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
    print("3/3 启动程序")
    print("桌面助手 v9.16")
    print("=" * 60)
    print()
    print("对应旧流程：3启动.bat")
    print()
    print("启动前建议已完成：")
    print("  1安装-组件.bat  依赖+运行库+体检")
    print("  2安装-模型.bat  至少 OCR / 你需要的语音模型")
    print()

    if not MAIN.is_file():
        print("[失败] 找不到 mainv916.py")
        return 1

    print("[检查] 快速组件体检...")
    subprocess.call([PY, str(ROOT / "tools" / "setup_components.py"), "--check"])
    print()
    print("若截图 OCR 未就绪，可先：")
    print("  python tools/setup_components.py --only paddle-cpu --yes")
    print("  python tools/setup_components.py --only paddleocr --yes")
    print("  python tools/download_models.py --only paddleocr")
    print()
    pause("按回车启动程序...")
    print("启动 mainv916.py ...")
    return subprocess.call([PY, str(MAIN)])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消。")
        raise SystemExit(130)
