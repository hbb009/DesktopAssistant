# -*- coding: utf-8 -*-
"""Step 1: install runtime components (old steps 1+4+5)."""
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


def pause(msg: str = "按任意键继续..."):
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
    print("本脚本合并了原先分步流程中的：")
    print("  旧1 安装-程序依赖")
    print("  旧4 安装-组件 ffmpeg/torch/paddle/OCR 等")
    print("  旧5 体检-组件")
    print()
    print("会装这些【运行组件】不含 model 大权重：")
    print("  [A] requirements.txt          程序界面与基础库")
    print("  [B] ffmpeg                    录屏 / 音视频合并")
    print("  [C] requirements-models.txt   语音/OCR 相关 pip")
    print("      含 paddleocr[doc-parser]，VL 必需")
    print("  [D] torch / torchaudio        语音克隆基础")
    print("  [E] paddlepaddle GPU或CPU     截图 OCR 底座")
    print("  [F] paddleocr[doc-parser]     PaddleOCR-VL 运行库")
    print("  [G] CosyVoice 源码 + 其 pip 依赖")
    print()
    print("不会下载：PaddleOCR-VL / whisper / CosyVoice 大权重")
    print("那些在 2安装-模型.bat")
    print()
    print("可选兜底：paddle-cpu / diagnose_dll / fix_ocr")
    print("-" * 60)
    pause("按回车开始...")

    print()
    print("[1/7] 检查 Python ...")
    print("Python:", PY)
    rc = run([PY, "--version"])
    if rc != 0:
        print("[失败] Python 不可用。请安装 3.10+ 并勾选 Add python.exe to PATH。")
        return 1
    print("[成功] Python 可用")

    print()
    print("[2/7] 升级 pip ...")
    if run([PY, "-m", "pip", "install", "-U", "pip"]) != 0:
        print("[失败] pip 升级失败，请检查网络后重试。")
        return 1
    print("[成功] pip 就绪")

    print()
    print("[3/7] 安装程序依赖 requirements.txt ...")
    print("对应旧：1安装-程序依赖.bat")
    if run([PY, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")]) != 0:
        print("[失败] requirements.txt 安装失败。请检查网络/镜像后重试。")
        return 1
    print("[成功] requirements.txt 完成")

    print()
    print("[4/7] 安装前体检 看缺什么 ...")
    print("对应旧：5体检-组件.bat")
    run([PY, str(ROOT / "tools" / "setup_components.py"), "--check"])
    print()
    print("上面表格仅供参考，接下来开始自动补齐运行组件。")
    pause("按回车继续安装...")

    print()
    print("[5/7] 自动安装运行组件 对应旧：4安装-组件.bat ...")
    print("内容：ffmpeg + deps + torch + paddle + paddleocr")
    print("      + CosyVoice 源码 + cosyvoice-deps")
    print("GPU 版 paddle 若导入失败，脚本会按默认改走 CPU 版。")
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
        print(f"[警告] 自动安装退出码={sc}，可能有项目未装全。")
    else:
        print("[成功] 自动安装脚本结束")

    print()
    print("[6/7] 安装后体检 + OCR 真测 ...")
    print("截图 OCR 会真测 PaddleOCRVL，不再只看包名；首次可能较慢")
    print("请看表格每一行是就绪还是缺口：录屏 / 语音 / 截图OCR / 语音克隆")
    ck = run([PY, str(ROOT / "tools" / "setup_components.py"), "--check"])

    print()
    print("[判定说明]")
    print("  - 截图 OCR 就绪 = paddle + paddleocr[doc-parser] + 真测通过")
    print("    大权重可在第2步再下；缺权重时表里会提示")
    print("  - 语音克隆需 torch + Cosy源码 + cosy依赖；约9GB权重在第2步")
    print("  - GPU 版 paddle 反复失败时用：--only paddle-cpu")
    print()
    print("[7/7] 兜底与下一步")
    print("-" * 60)

    need_repair = sc != 0 or ck != 0
    if need_repair:
        print("[警告] 自动安装或体检未完全通过。")
        print("建议现在打开交互菜单 对应旧4安装-组件，按提示补缺。")
        print("也可稍后执行：")
        print("  python tools/setup_components.py --check")
        print("  python tools/setup_components.py")
        print("  python tools/setup_components.py --only paddle-cpu --yes")
        print("  python tools/diagnose_dll.py ocr")
        print()
        open_menu = ask_yes("现在打开交互式组件菜单", default_yes=True)
    else:
        print("[成功] 第 1 步流程走完。请再确认上方体检表。")
        print()
        print("若「截图 OCR」仍不是就绪：")
        print("  1) python tools/setup_components.py --only paddle-cpu --yes")
        print("  2) python tools/setup_components.py --only paddleocr --yes")
        print("  3) python tools/diagnose_dll.py ocr")
        print("  4) tools/fix_ocr.bat")
        print("  若报 c10.dll / WinError 1114：")
        print("     python tools/diagnose_dll.py ocr")
        print("     python tools/setup_components.py --only paddle-cpu --yes")
        print("     python tools/setup_components.py --only paddleocr --yes")
        print()
        print("下一步：双击  2安装-模型.bat  下载开源模型权重")
        print("若刚装了 ffmpeg，之后请新开命令行再启动程序。")
        print("-" * 60)
        print()
        open_menu = ask_yes("是否打开交互式组件菜单再修一次 旧4", default_yes=False)

    if open_menu:
        print()
        print("进入交互菜单 可修录屏/语音/OCR/克隆/全部 ...")
        run([PY, str(ROOT / "tools" / "setup_components.py")])
        print()
        print("菜单结束后再次体检：")
        run([PY, str(ROOT / "tools" / "setup_components.py"), "--check"])

    print()
    if need_repair:
        print("第 1 步结束，仍有警告。")
        return 1
    print("第 1 步结束。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消。")
        raise SystemExit(130)
