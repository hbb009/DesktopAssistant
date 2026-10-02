# -*- coding: utf-8 -*-
"""Step 3: launch only — no component OCR probe.

日常启动走 pythonw + CREATE_NO_WINDOW，不挂控制台、不等待界面退出。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
MAIN = ROOT / "mainv917.py"
CREATE_NO_WINDOW = 0x08000000


def _fix_stdio():
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _error_popup(msg: str) -> None:
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, msg, "Desktop Assistant", 0x10)
            return
        except Exception:
            pass
    print(msg)


def _gui_python(py: str) -> str:
    """优先 pythonw.exe，避免再弹出一个 Python 控制台。"""
    path = Path(py)
    if path.name.lower() == "pythonw.exe":
        return str(path)
    cand = path.with_name("pythonw.exe")
    if cand.is_file():
        return str(cand)
    return str(path)


def _spawn_main() -> None:
    exe = _gui_python(PY)
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    kwargs = {
        "args": [exe, str(MAIN)],
        "cwd": str(ROOT),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NO_WINDOW
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        kwargs["startupinfo"] = si
    subprocess.Popen(**kwargs)


def main() -> int:
    _fix_stdio()
    os.chdir(ROOT)
    if not MAIN.is_file():
        _error_popup("找不到 mainv917.py")
        return 1
    try:
        _spawn_main()
    except Exception as e:
        _error_popup("启动失败：%s" % e)
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
