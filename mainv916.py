import sys
import os
import json
import ctypes

# ── 打包后语音转写子进程：必须在 import PyQt5 之前退出 ──────────────
# Windows 上 QApplication/Qt DLL 与 ctranslate2 同进程会 access violation。
# 开发：python -m utils.whisper_job <meta.json>
# 打包：mainv916.exe --whisper-job <meta.json>
if "--whisper-job" in sys.argv:
    _wj_args = [a for a in sys.argv[1:] if a != "--whisper-job"]
    from utils.whisper_job import main as _whisper_job_main
    raise SystemExit(_whisper_job_main(_wj_args))

# ── 截图 OCR 子进程：同样必须在 import PyQt5 之前退出 ──────────────
# 主进程里的 Qt 会与 Paddle / OpenCV 抢 DLL，
# 常见表现是空 ImportError，「检测」和识字都失败。
# 开发：python -m utils.ocr_job <meta.json>
# 打包：mainv916.exe --ocr-job <meta.json>
if "--ocr-job" in sys.argv:
    _oj_args = [a for a in sys.argv[1:] if a != "--ocr-job"]
    from utils.ocr_job import main as _ocr_job_main
    raise SystemExit(_ocr_job_main(_oj_args))

# 常驻 OCR 服务（管线只加载一次，后续截图识别复用）：
# 打包：mainv916.exe --ocr-server
if "--ocr-server" in sys.argv:
    from utils.ocr_job import server_main as _ocr_server_main
    raise SystemExit(_ocr_server_main())

# ── 语音克隆子进程：同样必须在 import PyQt5 之前退出 ──────────────
# 主进程里的 Qt 会与 PyTorch / onnxruntime 抢 DLL。
# 开发：python -m utils.cosyvoice_job <meta.json>
# 打包：mainv916.exe --cosyvoice-job <meta.json>
if "--cosyvoice-job" in sys.argv:
    _cj_args = [a for a in sys.argv[1:] if a != "--cosyvoice-job"]
    from utils.cosyvoice_job import main as _cosyvoice_job_main
    raise SystemExit(_cosyvoice_job_main(_cj_args))

# ── 界面缩放：必须在创建 QApplication 之前写入 QT_SCALE_FACTOR ──────────
# 「关于」弹窗改倍数 → 写 user.txt → 重启；此处读偏好并设全局缩放。
# v9.13 重构时曾漏掉这段，导致选 1.5× 等后重启仍停在 1×；入口为 mainv916.py。
_UI_SCALE_OPTIONS = (0.75, 1.0, 1.25, 1.5)


def _app_root_early() -> str:
    if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _peek_ui_scale() -> float:
    """不依赖 Qt / user_prefs，尽早读 user.txt 里的缩放倍数。"""
    root = _app_root_early()
    for rel in (("data", "user.txt"), ("records", "user.txt")):
        path = os.path.join(root, *rel)
        try:
            if not os.path.isfile(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                continue
            raw = (data.get("ui") or {}).get("scale", 1.0)
            s = float(raw)
        except Exception:
            continue
        s = max(0.5, min(3.0, s))
        best = min(_UI_SCALE_OPTIONS, key=lambda x: abs(x - s))
        if abs(best - s) < 0.05:
            s = float(best)
        return s
    return 1.0


def _apply_qt_scale_factor(scale: float) -> None:
    """写入环境变量；只能影响此后创建的 QApplication。"""
    try:
        s = float(scale)
    except (TypeError, ValueError):
        s = 1.0
    # 关掉系统 DPI 自动缩放，避免与 QT_SCALE_FACTOR 叠乘
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "0"
    if abs(s - 1.0) < 1e-6:
        os.environ["QT_SCALE_FACTOR"] = "1"
        return
    t = f"{s:.4f}".rstrip("0").rstrip(".")
    os.environ["QT_SCALE_FACTOR"] = t


_EARLY_UI_SCALE = _peek_ui_scale()
_apply_qt_scale_factor(_EARLY_UI_SCALE)
# ─────────────────────────────────────────────────────────────────

# 旧 records/gallery/cards 收进 data/，须在写日志之前完成
try:
    from utils.app_paths import ensure_data_layout
    ensure_data_layout()
except Exception:
    pass

# ── 硬崩溃兜底：Qt/原生访问违规时把 Python 栈写进 data/crash_*.log ──────────
# 语音克隆等子进程只吃 CPU/显存，主进程的“程序突然消失”大多是 Qt
# 卸载或线程竞争触发的原生崩溃，没有 Python traceback。这里注册 faulthandler，
# 一旦发生 access violation，把当时所有线程的 Python 栈落到 data/ 里。
# 启动时只保留 2 小时内，以及最近 2 次开启留下的 crash 日志，其余清掉。
_crash_purged = 0
try:
    import faulthandler
    from datetime import datetime as _dt
    from utils.app_paths import data_dir as _data_dir
    from utils.logger import purge_old_crash_logs
    _crash_dir = _data_dir()
    os.makedirs(_crash_dir, exist_ok=True)
    _crash_log = open(
        os.path.join(_crash_dir, "crash_%s.log" % _dt.now().strftime("%Y%m%d_%H%M%S")),
        "w", encoding="utf-8",
    )
    faulthandler.enable(file=_crash_log, all_threads=True)
    _crash_purged = purge_old_crash_logs(_crash_dir, keep_path=_crash_log.name)
except Exception:
    pass

# 屏蔽 Qt Windows 剪贴板轮询刷屏
from PyQt5.QtCore import QLoggingCategory
QLoggingCategory.setFilterRules("qt.qpa.mime.warning=false")

from utils.logger import get_logger

log = get_logger("main")
if _crash_purged:
    log.info("已清理过期 crash 日志 %s 个", _crash_purged)


def _drop_empty_crash_file():
    """正常退出 / Python 异常退出时：本次 crash 文件仍为空就删掉。

    启动总会预建一个空文件给 faulthandler 兜底；真崩溃会有栈内容、
    非空保留，只有从没崩过的空占位会被清掉，避免空文件堆积。
    """
    try:
        p = _crash_log.name
    except Exception:
        return
    try:
        if os.path.getsize(p) <= 0:
            os.remove(p)
    except Exception:
        pass


def _is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        log.exception("检测管理员权限失败，按非管理员处理")
        return False


def _relaunch_as_admin():
    """用管理员权限重新启动当前脚本，然后退出当前进程。

    默认不再自动提权（减少 UAC 弹窗与杀软误报）。
    需要时：python mainv916.py --as-admin
    目录映射页的 mklink 也可单独勾选「以管理员权限执行」。
    """
    script = os.path.abspath(sys.argv[0])
    # 去掉 --as-admin，避免提权后再次递归
    rest = [a for a in sys.argv[1:] if a != "--as-admin"]
    params = " ".join(f'"{a}"' for a in rest)
    log.info("请求管理员权限重新启动: %s", script)
    ret = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable,
        f'"{script}" {params}',
        None, 1
    )
    if not ret or ret <= 32:
        log.error("ShellExecuteW runas 失败 ret=%s", ret)
    sys.exit(0)


# 仅在显式传入 --as-admin 时提权；默认普通用户启动
if "--as-admin" in sys.argv and not _is_admin():
    _relaunch_as_admin()

from PyQt5.QtWidgets import QApplication
from ui_main import MainWindow

if __name__ == "__main__":
    log.info(
        "启动桌面助手 v9.16 admin=%s frozen=%s argv=%s "
        "ui_scale=%s QT_SCALE_FACTOR=%s",
        _is_admin(),
        getattr(sys, "frozen", False),
        sys.argv[1:],
        _EARLY_UI_SCALE,
        os.environ.get("QT_SCALE_FACTOR", ""),
    )
    app = QApplication(sys.argv)
    try:
        win = MainWindow()
        win.show()
        code = app.exec_()
    except Exception:
        log.exception("主程序异常退出")
        _drop_empty_crash_file()
        raise
    _drop_empty_crash_file()
    log.info("进程退出 code=%s", code)
    sys.exit(code)
