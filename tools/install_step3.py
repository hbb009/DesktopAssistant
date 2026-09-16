# -*- coding: utf-8 -*-
"""Step 3: launch only — no component OCR probe."""
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


def main() -> int:
    _fix_stdio()
    os.chdir(ROOT)
    print("=" * 60)
    print("3/3 启动程序")
    print("桌面助手 v9.16")
    print("=" * 60)
    print()
    print("本步只启动，不做组件体检 / OCR 真测。")
    print("检测请到软件「系统总览」点各组件的「检测」，")
    print("或：python tools/setup_components.py --check-deep")
    print()

    if not MAIN.is_file():
        print("[失败] 找不到 mainv916.py")
        try:
            input("按回车关闭...")
        except EOFError:
            pass
        return 1

    print("启动 mainv916.py ...")
    return subprocess.call([PY, str(MAIN)])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消。")
        raise SystemExit(130)
