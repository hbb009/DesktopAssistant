# -*- coding: utf-8 -*-
"""deskassist_runtime.py — portable Python / MEIPASS helpers for worker launchers."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


def app_root() -> Path:
    """Writable app root: exe directory when frozen, project root in dev."""
    try:
        from utils.app_paths import app_root as _ar
        return Path(_ar())
    except Exception:
        if bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS"):
            return Path(sys.executable).resolve().parent
        # utils/ -> project root
        return Path(__file__).resolve().parents[1]


def meipass() -> Path:
    """PyInstaller unpack dir, or onedir `_internal` beside the exe."""
    mp = getattr(sys, "_MEIPASS", None)
    if mp:
        return Path(mp)
    return app_root() / "_internal"


def find_python() -> Optional[str]:
    """Prefer env overrides, then bundled runtime\\python, then system Python 3.11."""
    cands = []
    for key in (
        "DESKASSIST_PYTHON",
        "OCR_PYTHON",
        "WHISPER_PYTHON",
        "COSYVOICE_PYTHON",
        "PYTHON_EXE",
    ):
        v = os.environ.get(key)
        if v:
            cands.append(Path(v))

    # Bundled portable runtime MUST win over D:\\Python311 when present
    cands.append(app_root() / "runtime" / "python" / "python.exe")

    cands.extend(
        [
            Path(r"D:\Python311\python.exe"),
            Path(r"C:\Python311\python.exe"),
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Programs"
            / "Python"
            / "Python311"
            / "python.exe",
        ]
    )
    for p in os.environ.get("PATH", "").split(os.pathsep):
        if p:
            cands.append(Path(p) / "python.exe")

    seen = set()
    for c in cands:
        try:
            c = c.resolve()
        except Exception:
            continue
        key = str(c).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            if c.is_file():
                return str(c)
        except Exception:
            continue
    return None


# Back-compat aliases used by earlier PYZ patches
def _deskassist_find_python() -> Optional[str]:
    return find_python()


def _deskassist_meipass() -> Path:
    return meipass()
