# -*- coding: utf-8 -*-
# utils/translate_opus.py
# 主进程侧：拉起无 Qt 的 translate_job 常驻子进程，供提示词页中/英预览调用。
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

_DAEMON = None
_DAEMON_LOCK = threading.Lock()


def opus_model_dirs() -> Dict[str, str]:
    try:
        from utils.app_paths import model_dir
        root = model_dir()
    except Exception:
        root = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "model"
        )
    return {
        "zh-en": os.path.join(root, "opus-mt-zh-en"),
        "en-zh": os.path.join(root, "opus-mt-en-zh"),
    }


def _ready(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    if not os.path.isfile(os.path.join(path, "config.json")):
        return False
    for name in ("pytorch_model.bin", "model.safetensors"):
        if os.path.isfile(os.path.join(path, name)):
            return True
    try:
        for fn in os.listdir(path):
            fp = os.path.join(path, fn)
            if (
                os.path.isfile(fp)
                and fn.endswith((".bin", ".safetensors"))
                and os.path.getsize(fp) > 1_000_000
            ):
                return True
    except OSError:
        pass
    return False


def opus_bundle_ready() -> bool:
    dirs = opus_model_dirs()
    return _ready(dirs["zh-en"]) or _ready(dirs["en-zh"])


def opus_direction_ready(direction: str) -> bool:
    d = "zh-en" if direction == "zh-en" else "en-zh"
    return _ready(opus_model_dirs()[d])


def _spawn_daemon():
    import subprocess

    global _DAEMON
    if _DAEMON is not None and _DAEMON.poll() is None:
        return _DAEMON
    try:
        from utils.app_paths import app_root, is_frozen
        frozen = is_frozen()
        root = app_root()
    except Exception:
        frozen = bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if frozen:
        cmd = [sys.executable, "--translate-server"]
    else:
        cmd = [sys.executable, "-m", "utils.translate_job", "--server"]

    env = os.environ.copy()
    env.setdefault("OMP_NUM_THREADS", "1")
    env.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    env["DESKASSIST_TRANSLATE_JOB"] = "1"
    env.pop("QT_PLUGIN_PATH", None)
    env.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)

    run_kw: Dict[str, Any] = dict(
        cwd=root,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if sys.platform == "win32":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            run_kw["startupinfo"] = si
        except Exception:
            pass
    try:
        p = subprocess.Popen(cmd, **run_kw)
    except Exception as e:
        log.warning("启动翻译常驻进程失败: %s", e)
        _DAEMON = None
        return None
    _DAEMON = p
    return p


def translate_batch(
    texts: List[str],
    *,
    direction: str = "zh-en",
    force_cpu: bool = False,
    timeout: float = 180.0,
) -> List[str]:
    """阻塞调用：返回与 texts 等长的译文。失败抛 RuntimeError。"""
    direction = "zh-en" if direction == "zh-en" else "en-zh"
    if not opus_direction_ready(direction):
        raise RuntimeError(
            f"缺少 opus-MT 模型（需要 model/opus-mt-{'zh-en' if direction == 'zh-en' else 'en-zh'}）"
        )
    with _DAEMON_LOCK:
        daemon = _spawn_daemon()
        if daemon is None or daemon.stdin is None:
            raise RuntimeError("翻译常驻进程不可用")
        td = tempfile.mkdtemp(prefix="da_tr_")
        meta_path = os.path.join(td, "meta.json")
        out_path = os.path.join(td, "out.json")
        payload = {
            "direction": direction,
            "texts": [str(x or "") for x in texts],
            "out": out_path,
            "force_cpu": bool(force_cpu),
        }
        try:
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            daemon.stdin.write(meta_path + "\n")
            daemon.stdin.flush()
            deadline = time.time() + float(timeout)
            while time.time() < deadline:
                if daemon.poll() is not None:
                    err = ""
                    try:
                        err = (daemon.stderr.read() or "")[-800:]
                    except Exception:
                        pass
                    raise RuntimeError(
                        f"翻译进程退出 code={daemon.returncode} {err}"
                    )
                if os.path.isfile(out_path):
                    try:
                        with open(out_path, encoding="utf-8") as f:
                            data = json.load(f)
                    except json.JSONDecodeError:
                        time.sleep(0.05)
                        continue
                    if not isinstance(data, dict):
                        raise RuntimeError("翻译返回非对象")
                    if not data.get("ok"):
                        raise RuntimeError(str(data.get("error") or "翻译失败"))
                    outs = data.get("texts")
                    if not isinstance(outs, list) or len(outs) != len(texts):
                        raise RuntimeError("翻译结果条数不匹配")
                    return [str(x or "") for x in outs]
                time.sleep(0.05)
            raise RuntimeError(f"翻译超时（>{timeout:g}s）")
        finally:
            try:
                shutil.rmtree(td, ignore_errors=True)
            except Exception:
                pass


# ---- Qt 侧异步封装（仅主进程 import）----
def make_translate_worker(parent=None):
    from PyQt5.QtCore import QThread, pyqtSignal

    class _Worker(QThread):
        finished_ok = pyqtSignal(list, str)  # texts, direction
        finished_err = pyqtSignal(str, str)  # message, direction

        def __init__(self, texts: List[str], direction: str, force_cpu: bool = False, parent=None):
            super().__init__(parent)
            self._texts = list(texts)
            self._direction = direction
            self._force_cpu = force_cpu

        def run(self):
            try:
                outs = translate_batch(
                    self._texts,
                    direction=self._direction,
                    force_cpu=self._force_cpu,
                    timeout=180.0,
                )
                self.finished_ok.emit(outs, self._direction)
            except Exception as e:
                self.finished_err.emit(str(e), self._direction)

    return _Worker
