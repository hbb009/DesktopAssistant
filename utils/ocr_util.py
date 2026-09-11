# utils/ocr_util.py
# ---------------------------------------------------------------------------
# 截图编辑 · 本地文字识别（PaddleOCR-VL-1.6）
# 权重：model/PaddleOCR-VL-1.6
# 推理走无 Qt 子进程（ocr_job），避免 Paddle / OpenCV 与主进程 Qt 抢 DLL。
# ---------------------------------------------------------------------------

from __future__ import annotations

import atexit
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from typing import Any, Dict, Optional, Tuple

from utils.logger import get_logger

log = get_logger(__name__)

_MAX_EDGE = 2048

# ── OCR 常驻子进程：管线（含 1.9GB 权重）只加载一次，后续请求复用 ──────
_OCR_DAEMON_LOCK = threading.Lock()
_OCR_DAEMON = None  # subprocess.Popen | None


def _kill_ocr_daemon() -> None:
    """主进程退出 / 常驻进程异常时收尾。"""
    global _OCR_DAEMON
    d = _OCR_DAEMON
    _OCR_DAEMON = None
    if d is None:
        return
    try:
        if d.poll() is None:
            try:
                if d.stdin is not None:
                    d.stdin.close()
            except Exception:
                pass
            try:
                d.terminate()
            except Exception:
                pass
            try:
                d.wait(timeout=5)
            except Exception:
                try:
                    d.kill()
                except Exception:
                    pass
    except Exception:
        pass


atexit.register(_kill_ocr_daemon)

# 探测缓存：None=未测 / True=可加载 / False=失败
_probe_ok: Optional[bool] = None
_probe_msg: str = ""


def _pip_python_display() -> str:
    try:
        from utils.app_paths import pip_python
        return pip_python()
    except Exception:
        return sys.executable or "python"


def _pip_prefix() -> list:
    try:
        from utils.app_paths import pip_cmd_prefix
        return list(pip_cmd_prefix())
    except Exception:
        return [sys.executable or "python", "-m", "pip"]


def OCR_PIP_CMD() -> str:
    py = _pip_python_display()
    return (
        f'"{py}" -m pip install -U "paddleocr[doc-parser]>=3.6.0"\n'
        f'  "{py}" -m pip install paddlepaddle-gpu==3.2.1 '
        f"-i https://www.paddlepaddle.org.cn/packages/stable/cu126/"
    )


# 兼容旧名（关于页 / 截图页仍可能读这个字符串属性）
OCR_PIP_PACKAGE = "paddleocr[doc-parser]>=3.6.0"


def _pkg_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def find_vl_model_dir() -> str:
    """本地 PaddleOCR-VL-1.6 权重目录。"""
    try:
        from utils.app_paths import model_dir
        root = model_dir()
    except Exception:
        root = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "model"
        )
    cands = [
        os.path.join(root, "PaddleOCR-VL-1.6"),
        os.path.join(root, "PaddleOCR-VL"),
    ]
    for p in cands:
        if _looks_like_vl_weights(p):
            return p
    try:
        for name in os.listdir(root):
            p = os.path.join(root, name)
            if name.lower().startswith("paddleocr-vl") and _looks_like_vl_weights(p):
                return p
    except Exception:
        pass
    return os.path.join(root, "PaddleOCR-VL-1.6")


def _looks_like_vl_weights(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    st = os.path.join(path, "model.safetensors")
    cfg = os.path.join(path, "config.json")
    return os.path.isfile(st) and os.path.getsize(st) > 1024 * 1024 and os.path.isfile(cfg)


def has_vl_weights() -> bool:
    return _looks_like_vl_weights(find_vl_model_dir())


def has_paddleocr_runtime() -> bool:
    return _pkg_available("paddleocr") and _pkg_available("paddle")


def is_local_ocr_ready() -> bool:
    """权重 + paddleocr 都在才允许点截图 OCR。"""
    return has_vl_weights() and has_paddleocr_runtime()


def reset_ocr_cache() -> None:
    global _probe_ok, _probe_msg
    _probe_ok = None
    _probe_msg = ""
    # 强制重测时也换掉常驻进程，确保用最新的安装/权重
    _kill_ocr_daemon()


def _exc_text(err: BaseException) -> str:
    raw = str(err).strip()
    if raw:
        return f"{type(err).__name__}: {raw}"
    return f"{type(err).__name__}: {err!r}"


def install_hint_text() -> str:
    model = find_vl_model_dir()
    py = _pip_python_display()
    return (
        "截图 OCR 使用本地 PaddleOCR-VL-1.6。\n\n"
        f"1) 权重（约 1.9GB）：{model}\n"
        "   已放入则跳过。\n\n"
        "2) 运行库（PaddlePaddle 3.2.1+ 与 paddleocr）：\n"
        f'  "{py}" -m pip install paddlepaddle-gpu==3.2.1 '
        "-i https://www.paddlepaddle.org.cn/packages/stable/cu126/\n"
        f'  "{py}" -m pip install -U "paddleocr[doc-parser]>=3.6.0"\n\n'
        "CPU 可改装 paddlepaddle==3.2.1（无 -gpu）。\n"
        "装好后回「系统总览 → 截图 OCR」点「检测」。未就绪时点「安装教程」。"
    )


def missing_local_ocr_message() -> str:
    if not has_vl_weights():
        return (
            "未找到 PaddleOCR-VL-1.6 权重。\n\n"
            "请把模型放到：\n"
            f"  {find_vl_model_dir()}"
        )
    if not has_paddleocr_runtime():
        return (
            "还缺 PaddleOCR 运行库。\n\n"
            "请打开「系统总览」→「截图 OCR」→「安装教程」。\n\n"
            + install_hint_text()
        )
    return "本地 OCR 尚未就绪。请到「系统总览 → 截图 OCR」检测。"


def local_ocr_status() -> Dict[str, Any]:
    """关于弹窗轻量状态（不 import paddle）。"""
    global _probe_ok, _probe_msg
    py = _pip_python_display()
    weights = has_vl_weights()
    runtime = has_paddleocr_runtime()
    model_dir = find_vl_model_dir()

    if weights and runtime:
        if _probe_ok is True:
            label, detail, ok = "可使用 · PaddleOCR-VL-1.6", "引擎已验证", True
        elif _probe_ok is False:
            label, detail, ok = "文件就绪但无法加载", (_probe_msg or "探测失败"), False
        else:
            label, detail, ok = (
                "已就绪 · PaddleOCR-VL-1.6",
                "点「检测」验证能否加载（首次较慢）",
                True,
            )
        return {
            "ok": ok and (_probe_ok is not False),
            "installed": True,
            "engine": "paddleocr-vl",
            "label": label,
            "detail": detail,
            "python": py,
            "model_dir": model_dir,
            "probe_ok": _probe_ok,
        }

    if weights and not runtime:
        return {
            "ok": False,
            "installed": False,
            "engine": "paddleocr-vl",
            "label": "缺运行库",
            "detail": "权重已在，请安装 paddleocr / PaddlePaddle",
            "python": py,
            "model_dir": model_dir,
            "probe_ok": None,
        }
    if runtime and not weights:
        return {
            "ok": False,
            "installed": False,
            "engine": "paddleocr-vl",
            "label": "未放入模型",
            "detail": "请把 PaddleOCR-VL-1.6 放到 model/",
            "python": py,
            "model_dir": model_dir,
            "probe_ok": None,
        }
    return {
        "ok": False,
        "installed": False,
        "engine": "none",
        "label": "未就绪",
        "detail": "需要权重 + paddleocr 运行库",
        "python": py,
        "model_dir": model_dir,
        "probe_ok": None,
    }


def _apply_probe_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    global _probe_ok, _probe_msg
    if payload.get("ok"):
        _probe_ok = True
        _probe_msg = "OK"
    else:
        _probe_ok = False
        _probe_msg = (payload.get("error") or payload.get("detail") or "OCR 子进程失败").strip()
    st = local_ocr_status()
    st["probe_msg"] = _probe_msg
    if payload.get("paddle"):
        st["paddle"] = payload.get("paddle")
    return st


def probe_local_ocr(*, force: bool = False) -> Dict[str, Any]:
    global _probe_ok, _probe_msg
    if not force and _probe_ok is not None:
        st = local_ocr_status()
        st["probe_msg"] = _probe_msg
        return st
    if not has_vl_weights() or not has_paddleocr_runtime():
        _probe_ok = False
        _probe_msg = missing_local_ocr_message()
        st = local_ocr_status()
        st["probe_msg"] = _probe_msg
        return st
    if force:
        reset_ocr_cache()
    try:
        payload = _run_ocr_job({"action": "probe"}, timeout=180)
        return _apply_probe_payload(payload)
    except Exception as e:
        log.warning("OCR 子进程探测失败: %s", _exc_text(e))
        _probe_ok = False
        _probe_msg = "无法在独立进程里加载 OCR。\n\n" + _exc_text(e)
        st = local_ocr_status()
        st["probe_msg"] = _probe_msg
        return st


def pixmap_to_png_path(pixmap, *, max_edge: int = _MAX_EDGE) -> str:
    """主线程调用：QPixmap → 临时 PNG 路径（可缩小）。"""
    from PyQt5.QtCore import Qt

    if pixmap is None or pixmap.isNull():
        raise ValueError("空图像")

    img = pixmap.toImage()
    w, h = img.width(), img.height()
    edge = max(w, h)
    if max_edge and edge > max_edge:
        scale = max_edge / float(edge)
        nw = max(1, int(w * scale))
        nh = max(1, int(h * scale))
        img = img.scaled(nw, nh, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    fd, path = tempfile.mkstemp(prefix="da_ocr_", suffix=".png")
    os.close(fd)
    if not img.save(path, "PNG"):
        try:
            os.remove(path)
        except Exception:
            pass
        raise RuntimeError("无法写入临时截图")
    return path


def _spawn_ocr_daemon():
    """拉起常驻 OCR 子进程；已存活则直接返回。"""
    import subprocess

    global _OCR_DAEMON
    if _OCR_DAEMON is not None and _OCR_DAEMON.poll() is None:
        return _OCR_DAEMON
    try:
        from utils.app_paths import app_root, is_frozen
        frozen = is_frozen()
        root = app_root()
    except Exception:
        frozen = bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    env = os.environ.copy()
    env.setdefault("OMP_NUM_THREADS", "1")
    env.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    env["DESKASSIST_OCR_JOB"] = "1"
    env.pop("QT_PLUGIN_PATH", None)
    env.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)

    if frozen:
        from utils.deskassist_runtime import find_python, meipass
        _py = find_python()
        _worker = meipass() / "ocr_worker" / "run.py"
        if _py and _worker.is_file():
            cmd = [_py, str(_worker), "--server"]
            env["OCR_MEIPASS"] = str(meipass())
        else:
            cmd = [sys.executable, "--ocr-server"]
    else:
        cmd = [sys.executable, "-m", "utils.ocr_job", "--server"]

    run_kw: Dict[str, Any] = dict(
        cwd=root,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
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
        log.warning("启动 OCR 常驻进程失败: %s", e)
        _OCR_DAEMON = None
        return None
    _OCR_DAEMON = p
    return p


def _run_ocr_daemon(meta: Dict[str, Any], *, timeout: float) -> Dict[str, Any]:
    """把请求交给常驻 OCR 进程，轮询 out.json 等结果（带超时与进程存活检测）。"""
    daemon = _spawn_ocr_daemon()
    if daemon is None:
        raise RuntimeError("OCR 常驻进程不可用")
    if daemon.stdin is None:
        raise RuntimeError("OCR 常驻进程管道不可用")

    td = tempfile.mkdtemp(prefix="da_ocr_req_")
    meta_path = os.path.join(td, "meta.json")
    out_path = os.path.join(td, "out.json")
    payload = dict(meta)
    payload["out"] = out_path
    payload.setdefault("model_dir", find_vl_model_dir())
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        daemon.stdin.write(meta_path + "\n")
        daemon.stdin.flush()
        deadline = time.time() + float(timeout)
        while time.time() < deadline:
            if daemon.poll() is not None:
                raise RuntimeError(f"OCR 常驻进程退出 code={daemon.returncode}")
            if os.path.isfile(out_path):
                with open(out_path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
                raise RuntimeError("OCR 常驻进程返回非对象")
            time.sleep(0.05)
        raise RuntimeError(f"OCR 常驻进程超时（>{timeout:g}s）")
    finally:
        try:
            shutil.rmtree(td, ignore_errors=True)
        except Exception:
            pass


def _run_ocr_job(meta: Dict[str, Any], *, timeout: int = 600) -> Dict[str, Any]:
    """OCR 请求入口：优先走常驻进程（管线复用，避免每次冷加载 1.9GB 权重），
    常驻失败则退回一次性子进程。"""
    with _OCR_DAEMON_LOCK:
        try:
            return _run_ocr_daemon(meta, timeout=float(timeout))
        except Exception as e:
            log.warning("OCR 常驻进程请求失败，退回一次性子进程: %s", e)
            _kill_ocr_daemon()
        return _run_ocr_job_oneshot(meta, timeout=timeout)


def _run_ocr_job_oneshot(meta: Dict[str, Any], *, timeout: int = 600) -> Dict[str, Any]:
    """一次性子进程（原 _run_ocr_job）：每次冷启动 Paddle 与权重。"""
    import subprocess

    td = tempfile.mkdtemp(prefix="da_ocr_job_")
    meta_path = os.path.join(td, "meta.json")
    out_path = os.path.join(td, "out.json")
    err_path = os.path.join(td, "err.txt")
    payload = dict(meta)
    payload["out"] = out_path
    payload.setdefault("model_dir", find_vl_model_dir())
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

        try:
            from utils.app_paths import app_root, is_frozen
            frozen = is_frozen()
            root = app_root()
        except Exception:
            frozen = bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        env = os.environ.copy()
        env.setdefault("OMP_NUM_THREADS", "1")
        env.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
        env["DESKASSIST_OCR_JOB"] = "1"
        env.pop("QT_PLUGIN_PATH", None)
        env.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)

        if frozen:
            from utils.deskassist_runtime import find_python, meipass
            _py = find_python()
            _worker = meipass() / "ocr_worker" / "run.py"
            if _py and _worker.is_file():
                cmd = [_py, str(_worker), meta_path]
                env["OCR_MEIPASS"] = str(meipass())
            else:
                cmd = [sys.executable, "--ocr-job", meta_path]
        else:
            cmd = [sys.executable, "-m", "utils.ocr_job", meta_path]

        run_kw: Dict[str, Any] = dict(
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        if sys.platform == "win32":
            run_kw["creationflags"] = getattr(
                subprocess, "CREATE_NO_WINDOW", 0x08000000
            )
            try:
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = 0
                run_kw["startupinfo"] = si
            except Exception:
                pass

        proc = subprocess.run(cmd, **run_kw)
        data = None
        if os.path.isfile(out_path):
            try:
                with open(out_path, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = None
        if isinstance(data, dict):
            return data

        detail = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()
        if os.path.isfile(err_path):
            try:
                extra = open(err_path, encoding="utf-8", errors="replace").read().strip()
                if extra:
                    detail = extra
            except Exception:
                pass
        if int(proc.returncode) in (-1073741819, 3221225477):
            detail = (
                (detail + "\n") if detail else ""
            ) + "OCR 子进程 access violation（请确认 ocr_job 未 import PyQt5）"
        raise RuntimeError(
            f"OCR 子进程失败 code={proc.returncode}\n{(detail or '无输出')[-1500:]}"
        )
    finally:
        try:
            shutil.rmtree(td, ignore_errors=True)
        except Exception:
            pass


def ocr_image_path(png_path: str) -> Tuple[str, str]:
    """对磁盘 PNG 做本地文字识别（可在工作线程调用）。"""
    if not png_path or not os.path.isfile(png_path):
        return "❌ 无有效图像", "none"

    if not is_local_ocr_ready():
        return "❌ " + missing_local_ocr_message(), "none"

    try:
        payload = _run_ocr_job(
            {"action": "ocr", "image": os.path.abspath(png_path)},
            timeout=600,
        )
    except Exception as e:
        log.warning("OCR 子进程识别失败: %s", _exc_text(e))
        return f"❌ OCR 子进程失败\n\n{_exc_text(e)}", "none"
    if payload.get("ok"):
        text = (payload.get("text") or "").strip() or "（未识别到文字）"
        return text, payload.get("engine") or "paddleocr-vl"
    err = (payload.get("error") or "引擎加载失败").strip()
    tb = (payload.get("traceback") or "").strip()
    if tb and tb not in err:
        err = err + "\n\n" + tb[-800:]
    return f"❌ 本地 OCR 不可用\n\n{err}", "none"


def ocr_pixmap(pixmap) -> Tuple[str, str]:
    if pixmap is None or pixmap.isNull():
        return "❌ 无有效图像", "none"
    path = None
    try:
        path = pixmap_to_png_path(pixmap)
        return ocr_image_path(path)
    except Exception as e:
        return f"❌ 准备图像失败：{e}", "none"
    finally:
        if path:
            try:
                os.remove(path)
            except Exception:
                pass


def ocr_engine_hint() -> str:
    return local_ocr_status().get("label") or "PaddleOCR-VL-1.6"


def pip_install_args() -> list:
    return _pip_prefix() + ["install", "-U", "paddleocr[doc-parser]>=3.6.0"]


def can_run_inapp_pip() -> bool:
    try:
        from utils.app_paths import is_frozen
        return not is_frozen()
    except Exception:
        return not (getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"))
