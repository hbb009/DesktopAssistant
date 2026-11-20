"""Locate the optional isolated Whisper runtime and its worker."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from utils.app_paths import app_root, resource_root


def isolated_whisper_command():
    root = Path(app_root()) / "runtime" / "whisper"
    if os.name == "nt":
        python = root / "Scripts" / "python.exe"
        packages = root / "Lib" / "site-packages"
    else:
        python = root / "bin" / "python"
        packages = root / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    worker = Path(resource_root()) / "utils" / "whisper_job.py"
    if not worker.is_file():
        worker = root / "whisper_job.py"
    if python.is_file() and worker.is_file() and (packages / "faster_whisper" / "__init__.py").is_file():
        return [str(python), str(worker)]
    return None


def whisper_available():
    return isolated_whisper_command() is not None or importlib.util.find_spec("faster_whisper") is not None
