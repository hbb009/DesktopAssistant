# utils/auk_client.py
# 主进程侧：拉起 AuK 子进程并来回传 JSON。不要在这里 import torch。
# 另含结果行首文字的任务索引（data/auk/tasks.jsonl），见 record_task_title。

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading

logger = logging.getLogger("DesktopAssistant.AukClient")


def model_paths() -> dict:
    try:
        from utils.app_paths import model_dir
        root = model_dir()
    except Exception:
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "model")
    auk = os.path.join(root, "AuK")
    qwen = os.path.join(root, "Qwen2.5-Omni-3B")
    src = os.path.join(root, "AuK-src", "src")
    ckpt = os.path.join(auk, "auk_base.safetensors")
    cfg = os.path.join(auk, "config.yaml")
    return {
        "auk": auk,
        "qwen": qwen,
        "src": src,
        "ckpt": ckpt,
        "config": cfg,
        "ready": all(os.path.isfile(p) for p in (ckpt, cfg)) and os.path.isdir(qwen) and os.path.isdir(src),
    }


def _task_index_path() -> str:
    """AUK 任务索引：结果行首的「任务名 素材名」跨重启靠它还原。"""
    try:
        from utils.app_paths import data_dir
        d = os.path.join(data_dir(), "auk")
    except Exception:
        d = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "auk"
        )
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "tasks.jsonl")


def record_task_title(out_path: str, title: str) -> None:
    """记一条「输出文件 → 行首文字」。同名覆盖，只留最近 500 条。"""
    name = os.path.basename((out_path or "").strip())
    title = " ".join((title or "").split())
    if not name or not title:
        return
    path = _task_index_path()
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if isinstance(rec, dict) and rec.get("f"):
                    rows.append(rec)
    except Exception:
        rows = []
    rows = [r for r in rows if str(r.get("f")) != name]
    rows.append({"f": name, "t": title})
    rows = rows[-500:]
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for rec in rows:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except Exception:
        logger.exception("写 AUK 任务索引失败")


def task_titles() -> dict:
    """{输出文件名(小写): 行首文字}。索引坏了就当没有，退回显示文件名。"""
    out = {}
    try:
        with open(_task_index_path(), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if not isinstance(rec, dict):
                    continue
                name = str(rec.get("f") or "").strip()
                title = str(rec.get("t") or "").strip()
                if name and title:
                    out[name.lower()] = title
    except Exception:
        return {}
    return out


def _job_cmd() -> list:
    try:
        from utils.app_paths import is_frozen
        frozen = is_frozen()
    except Exception:
        frozen = bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")
    if frozen:
        return [sys.executable, "--auk-job", "--serve"]
    return [sys.executable, "-m", "utils.auk_job", "--serve"]


def _app_cwd() -> str:
    try:
        from utils.app_paths import app_root
        return app_root()
    except Exception:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class AukClient:
    """常驻子进程。模型在子进程里只加载一次。"""

    def __init__(self):
        self.proc = None
        self.offload = None
        self._err = []
        self._lock = threading.Lock()

    def stop(self) -> None:
        proc = self.proc
        self.proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None and proc.stdin:
                proc.stdin.write(json.dumps({"cmd": "shutdown"}) + "\n")
                proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _ensure(self, cpu_offload: bool) -> None:
        if self.proc is not None and self.proc.poll() is None and self.offload == bool(cpu_offload):
            return
        self.stop()
        paths = model_paths()
        env = os.environ.copy()
        env.setdefault("OMP_NUM_THREADS", "1")
        env.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
        # 桌面程序本身往往不是 UTF-8 模式。不强制的话，子进程会按 GBK 读标准流，
        # 中文路径被拆错，文件明明在也会报找不到。
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env.pop("QT_PLUGIN_PATH", None)
        env.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)
        src = paths["src"]
        if os.path.isdir(src):
            env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
        kw = {
            "cwd": _app_cwd(),
            "env": env,
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
        }
        if sys.platform == "win32":
            kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        self.proc = subprocess.Popen(_job_cmd(), **kw)
        self.offload = bool(cpu_offload)
        self._err = []

        def _drain():
            try:
                for line in self.proc.stderr:
                    self._err.append(line.rstrip())
                    if len(self._err) > 40:
                        del self._err[:-40]
            except Exception:
                pass

        threading.Thread(target=_drain, daemon=True).start()

    def run(self, req: dict, timeout: int = 1200) -> dict:
        cpu_offload = bool(req.get("cpu_offload", True))
        with self._lock:
            self._ensure(cpu_offload)
            proc = self.proc
            try:
                proc.stdin.write(json.dumps(req, ensure_ascii=True) + "\n")
                proc.stdin.flush()
            except Exception as exc:
                self.stop()
                return {"ok": False, "error": f"子进程已退出：{exc}"}
            holder = {}

            def _read():
                try:
                    holder["line"] = proc.stdout.readline()
                except Exception as exc:
                    holder["err"] = exc

            th = threading.Thread(target=_read, daemon=True)
            th.start()
            th.join(timeout)
            if th.is_alive():
                self.stop()
                return {"ok": False, "error": f"等待超过 {timeout} 秒，已停下"}
            if proc.poll() not in (None, 0) and "line" not in holder:
                tail = "\n".join(self._err[-8:])
                return {"ok": False, "error": tail or "AuK 子进程退出了"}
            line = holder.get("line") or ""
            if not line.strip():
                tail = "\n".join(self._err[-8:])
                return {"ok": False, "error": tail or "子进程没有返回结果"}
            try:
                data = json.loads(line)
            except Exception:
                return {"ok": False, "error": line.strip()[:300]}
            if not data.get("ok") and self._err:
                data["log"] = "\n".join(self._err[-6:])
            return data
