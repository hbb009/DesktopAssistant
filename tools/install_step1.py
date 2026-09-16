# -*- coding: utf-8 -*-
"""Step 1: install runtime components only (no slow OCR VL probe)."""
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


def hr(title: str = ""):
    print()
    print("=" * 60)
    if title:
        print(title)
        print("=" * 60)


def pause(msg: str = "按回车继续..."):
    try:
        input(msg)
    except EOFError:
        pass


def run(cmd: list[str]) -> int:
    print("  $", " ".join(str(c) for c in cmd))
    try:
        return subprocess.call(cmd, cwd=str(ROOT))
    except FileNotFoundError:
        print("[失败] 找不到命令:", cmd[0])
        return 1


def ask_yes(prompt: str, default_yes: bool = True) -> bool:
    tip = "Y/n" if default_yes else "y/N"
    try:
        ans = input(f"{prompt} [{tip}]: ").strip().lower()
    except EOFError:
        return default_yes
    if not ans:
        return default_yes
    return ans in ("y", "yes", "是")


def main() -> int:
    _fix_stdio()
    os.chdir(ROOT)
    hr("1/3 安装组件 - 桌面助手 v9.16")
    print("本步只负责【安装】运行库，不做 PaddleOCR-VL 真测（真测很慢）。")
    print("检测请用：软件内「检测」或  python tools/setup_components.py --check-deep")
    print()
    print("会装：requirements / ffmpeg / models-pip / torch / paddle / paddleocr / CosyVoice 源码与依赖")
    print("不会下载大权重（在 2安装-模型.bat）")
    print("-" * 60)
    pause("按回车开始安装...")

    print()
    print("[1/5] 检查 Python ...")
    print("Python:", PY)
    if run([PY, "--version"]) != 0:
        print("[失败] 找不到可用 Python。请安装 3.10+ 并勾选 Add python.exe to PATH。")
        return 1
    print("[成功] Python 可用")

    print()
    print("[2/5] 升级 pip ...")
    if run([PY, "-m", "pip", "install", "-U", "pip"]) != 0:
        print("[失败] pip 升级失败。")
        return 1

    print()
    print("[3/5] 安装程序依赖 requirements.txt ...")
    if run([PY, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")]) != 0:
        print("[失败] requirements.txt 安装失败。")
        return 1
    print("[成功] requirements.txt 完成")

    print()
    print("[4/5] 安装运行组件（ffmpeg / deps / torch / paddle / paddleocr / CosyVoice）...")
    print("可能较久，请耐心等待，不要关闭窗口。")
    print()
    sc = run([
        PY, str(ROOT / "tools" / "setup_components.py"),
        "--yes",
        "--only", "ffmpeg",
        "--only", "deps",
        "--only", "torch",
        "--only", "paddle",
        "--only", "paddleocr",
        "--only", "cosyvoice-code",
        "--only", "cosyvoice-deps",
    ])
    print()
    if sc != 0:
        print(f"[警告] 自动安装退出码={sc}")
    else:
        print("[成功] 自动安装结束")

    print()
    print("[5/5] 轻量体检（只看文件/包，不初始化 OCR 模型）...")
    ck = run([PY, str(ROOT / "tools" / "setup_components.py"), "--check"])
    print()
    print("说明：上面「截图 OCR 文件就绪」不等于已做 VL 真测。")
    print("下一步：2安装-模型.bat 下载权重；启动后在软件里点「检测」做真测。")
    print("若装后软件 OCR 仍失败：")
    print("  python tools/diagnose_dll.py ocr")
    print("  python tools/setup_components.py --only paddle-cpu --yes")
    print("  python tools/setup_components.py --check-deep")
    print("-" * 60)

    if sc != 0 or ck != 0:
        if ask_yes("安装或轻量体检未完全通过，是否打开交互修复菜单", default_yes=True):
            run([PY, str(ROOT / "tools" / "setup_components.py")])
        print("第 1 步结束（仍有警告）。")
        return 1

    if ask_yes("是否打开交互式组件菜单再看一眼", default_yes=False):
        run([PY, str(ROOT / "tools" / "setup_components.py")])

    print("第 1 步结束。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消。")
        raise SystemExit(130)
