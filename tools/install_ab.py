# -*- coding: utf-8 -*-
"""GitHub 下载后的两条安装入口。

  python tools/install_ab.py basic   A 低配：程序组件，不装本地模型
  python tools/install_ab.py full    B 高配：组件 + 模型运行库 + 可自动下载的权重

退出码：0 完成；1 完成但有警告（仍可启动）；2 程序本体没装上（不要启动）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
SUPPORTED_PY = ((3, 10), (3, 11))

# 高配才下。体积小的在前，语音克隆约 9GB 放最后。已在 model/ 里的会跳过。
FULL_WEIGHTS = (
    "whisper-tiny",
    "whisper-small",
    "whisper-large",
    "opus-mt-zh-en",
    "opus-mt-en-zh",
    "paddleocr",
    "cosyvoice-code",
    "cosyvoice",
)

BASIC_COMPONENTS = ("ffmpeg", "deno")
FULL_COMPONENTS = BASIC_COMPONENTS + (
    "git",
    "deps",
    "torch",
    "paddle",
    "paddleocr",
    "cosyvoice-code",
    "cosyvoice-deps",
)


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


def run(cmd: list[str]) -> int:
    print("  $", " ".join(str(c) for c in cmd))
    try:
        return subprocess.call(cmd, cwd=str(ROOT))
    except FileNotFoundError:
        print("[失败] 找不到命令:", cmd[0])
        return 1


def pause(msg: str = "按回车开始安装..."):
    try:
        input(msg)
    except EOFError:
        pass


def check_python() -> bool:
    print("Python:", PY)
    if run([PY, "--version"]) != 0:
        print("[失败] 找不到可用 Python。请安装 3.10 或 3.11，并勾选 Add python.exe to PATH。")
        return False
    ver = sys.version_info[:2]
    if ver not in SUPPORTED_PY:
        print(f"[失败] 当前 Python 是 {ver[0]}.{ver[1]}，桌面助手只支持 Python 3.10 或 3.11。")
        print("       请安装对应版本并勾选 Add python.exe to PATH，然后新开一个命令行窗口再运行。")
        return False
    print(f"[成功] Python {ver[0]}.{ver[1]}")
    return True


def install_requirements() -> bool:
    if run([PY, "-m", "pip", "install", "-U", "pip"]) != 0:
        print("[失败] pip 升级失败。")
        return False
    if run([PY, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")]) != 0:
        print("[失败] requirements.txt 安装失败。程序本体还不能用。")
        return False
    print("[成功] 程序依赖已安装")
    return True


def install_components(keys: tuple[str, ...]) -> int:
    cmd = [PY, str(ROOT / "tools" / "setup_components.py"), "--yes"]
    for key in keys:
        cmd.extend(["--only", key])
    return run(cmd)


def install_weights() -> int:
    rc = 0
    script = str(ROOT / "tools" / "download_models.py")
    for key in FULL_WEIGHTS:
        one = run([PY, script, "--only", key, "--mirror", "modelscope"])
        if one != 0:
            print(f"[警告] 模型 {key} 未完成，退出码={one}。后面的模型会继续。")
            rc = 1
    return rc


def main() -> int:
    _fix_stdio()
    os.chdir(ROOT)
    profile = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    if profile not in ("basic", "full"):
        print("用法: python tools/install_ab.py basic|full")
        return 2

    if profile == "basic":
        hr("A 低配安装 - 桌面助手 v9.18")
        print("只装程序组件，不装本地模型。")
        print("会装：requirements.txt、ffmpeg、Deno")
        print("不装：torch、paddle、语音克隆依赖，也不下载 model/ 里的权重")
        print("装完可以用下载、速存、粘贴、文本、看图、阅读这些基本功能。")
        print("截图 OCR、语音转写、语音克隆、本地翻译需要再运行 B安装.bat。")
    else:
        hr("B 高配安装 - 桌面助手 v9.18")
        print("先装程序组件，再装模型运行库，并下载能自动获取的权重。")
        print("会下载：截图 OCR、三档语音转写、中英翻译、语音克隆（大约数 GB 到十几 GB）。")
        print("不会自动下载、需要自己放进 model/ 的：AuK、Qwen2.5-Omni-3B、BiRefNet、Upscayl。")
    print("-" * 60)
    pause()

    print()
    print("[1] 检查 Python ...")
    if not check_python():
        return 2

    print()
    print("[2] 安装程序依赖 ...")
    if not install_requirements():
        return 2

    warn = False
    if profile == "basic":
        print()
        print("[3] 安装 ffmpeg / Deno ...")
        code = install_components(BASIC_COMPONENTS)
        if code != 0:
            print(f"[警告] 系统工具未全部装上，退出码={code}")
            print("程序仍可启动。录屏需要 ffmpeg，部分视频下载需要 Deno。")
            warn = True
        print()
        print("低配安装结束。没有安装本地模型。")
        print("以后打开程序：双击 3启动-程序.bat")
        print("要用全部功能：双击 B安装.bat")
        return 1 if warn else 0

    print()
    print("[3] 安装模型运行库（torch / paddle / 语音克隆依赖，可能较久）...")
    code = install_components(FULL_COMPONENTS)
    if code != 0:
        print(f"[警告] 组件未全部装上，退出码={code}")
        warn = True

    print()
    print("[4] 下载模型权重到 model/ （已经有的会跳过）...")
    print("优先走 ModelScope，失败再试 Hugging Face。")
    if install_weights() != 0:
        warn = True

    print()
    if warn:
        print("高配安装结束，有警告。已经装上的功能可以直接用。")
    else:
        print("[成功] 高配安装结束。")
    print("仍要自己放进 model/ 才能用的：AuK、Qwen2.5-Omni-3B、BiRefNet、Upscayl。")
    print("以后打开程序：双击 3启动-程序.bat")
    return 1 if warn else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消。")
        raise SystemExit(130)
