# pages/page_dup_clean.py
# 同名清理：选目录 → 统计全部文件/文件夹 → 弹窗报告
# 「去除同名」：文件名相同且字节大小相同，才把修改日期更早的版本移入回收站

from __future__ import annotations

import ctypes
import os
import re
import subprocess
import time
from collections import defaultdict
from datetime import datetime

from PyQt5.QtCore import Qt, QSize, QThread, QEvent, QPoint, QRectF, QTimer, pyqtSignal
from PyQt5.QtGui import QPainter, QColor, QPen, QPixmap, QFont
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QDialog, QScrollArea, QFrame, QSizePolicy, QMessageBox,
    QLayout, QLineEdit, QProgressBar, QStyle,
)

from styles.style_all import (
    theme, tk, make_card, install_card_title, restyle_card_title, restyle_card_frame,
    apply_transparent_surface, apply_btn_download, apply_medium_button,
    apply_mini_button, MEDIUM_BUTTON_H,
    CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP,
    CARD_TITLE_BODY_GAP,
)
from utils.logger import get_logger
from utils.qthread_util import stop_qthread
from utils.flow_layout import FlowLayout, FlowLayoutHost

log = get_logger("dup_clean")

_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_FO_DELETE = 3
_FOF_SILENT = 0x0004
_FOF_NOCONFIRMATION = 0x0010
_FOF_ALLOWUNDO = 0x0040
_FOF_NOERRORUI = 0x0400
_REPORT_GROUP_CAP = 200
_MULTI_ROW_CAP = 300

# 扩展名过滤卡：参照文本编辑页的前缀符号卡
_EXT_MAX = 48
_EXT_MAX_LEN = 24
_EXT_CARD_H = 24
_EXT_CARD_MIN_W = 40
_EXT_CARD_MAX_W = 180
_EXT_DRAG_PX = 6
_EXT_GUIDE_W = 12
_EXT_SPACING = 4
_EXT_PALETTE = (
    "#3b82f6", "#8b5cf6", "#ec4899", "#ef4444", "#f97316",
    "#eab308", "#22c55e", "#14b8a6", "#06b6d4", "#f43f5e",
    "#6366f1", "#a855f7",
)


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_void_p),
        ("pTo", ctypes.c_void_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", ctypes.c_int),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_void_p),
    ]


def _fmt_size(n: int) -> str:
    n = max(0, int(n or 0))
    if n < 1024:
        return f"{n} B"
    units = ("KB", "MB", "GB", "TB")
    val = float(n)
    for u in units:
        val /= 1024.0
        if val < 1024 or u == units[-1]:
            if val >= 100:
                return f"{val:.0f} {u}"
            if val >= 10:
                return f"{val:.1f} {u}"
            return f"{val:.2f} {u}"
    return f"{n} B"


def _fmt_time(ts: float) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError, TypeError):
        return "—"


def _name_key(name: str) -> str:
    # Windows 文件名大小写不敏感；同名按小写归并
    if os.name == "nt":
        return (name or "").casefold()
    return name or ""


def normalize_ext(raw: str) -> str:
    """把用户输入收成过滤键：jpg / tar.gz（不含点、小写）。"""
    s = (raw or "").strip()
    if not s:
        return ""
    s = s.replace("＊", "*")
    while s.startswith("*"):
        s = s[1:].lstrip()
    s = s.lstrip(".").strip().casefold()
    if not s or len(s) > _EXT_MAX_LEN:
        return ""
    if s.startswith(".") or s.endswith("."):
        return ""
    if any(c in s for c in '\\/:*?"<>| \t'):
        return ""
    return s


def parse_ext_input(raw: str) -> list:
    """空格 / 逗号 / 分号分开，规范化后去重保序。"""
    text = (raw or "").replace("；", ";").replace("，", ",")
    parts = re.split(r"[,;\s]+", text)
    out = []
    for p in parts:
        e = normalize_ext(p)
        if e and e not in out:
            out.append(e)
    return out


def file_matches_ext_filter(name: str, filters) -> bool:
    """filters 为空则全部通过；否则匹配后缀（含 tar.gz 这类复合后缀）。"""
    if not filters:
        return True
    n = (name or "").casefold()
    ext = os.path.splitext(n)[1].lstrip(".")
    if ext in filters:
        return True
    for f in filters:
        if "." in f and n.endswith("." + f):
            return True
    return False


def _mix_hex(a: str, b: str, t: float) -> str:
    ca, cb = QColor(a), QColor(b)
    return QColor(
        int(ca.red() + (cb.red() - ca.red()) * t),
        int(ca.green() + (cb.green() - ca.green()) * t),
        int(ca.blue() + (cb.blue() - ca.blue()) * t),
    ).name()


def _ext_contrast_fg(bg: str) -> str:
    try:
        c = QColor(bg)
        lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
        return "#111111" if lum > 150 else "#ffffff"
    except Exception:
        return "#ffffff"


def _skip_entry(entry: os.DirEntry) -> bool:
    """跳过符号链接 / 联接，避免扫进循环目录。"""
    try:
        st = entry.stat(follow_symlinks=False)
        attrs = int(getattr(st, "st_file_attributes", 0) or 0)
        if attrs & _FILE_ATTRIBUTE_REPARSE_POINT:
            return True
    except OSError:
        pass
    try:
        if entry.is_symlink():
            return True
    except OSError:
        return True
    return False


def _send_to_recycle(path: str) -> None:
    """把单个文件移入回收站；失败时保留文件。"""
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(path)
    if os.name == "nt":
        # pFrom 必须是双 NUL 结尾；c_wchar_p 会在第一个 \\0 处截断，所以用缓冲区地址
        buf = ctypes.create_unicode_buffer(os.path.abspath(path) + "\0")
        op = _SHFILEOPSTRUCTW()
        op.hwnd = None
        op.wFunc = _FO_DELETE
        op.pFrom = ctypes.addressof(buf)
        op.pTo = None
        op.fFlags = _FOF_ALLOWUNDO | _FOF_NOCONFIRMATION | _FOF_SILENT | _FOF_NOERRORUI
        op.fAnyOperationsAborted = 0
        rc = int(ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) or 0)
        if rc == 0 and not op.fAnyOperationsAborted:
            return
        if op.fAnyOperationsAborted:
            raise OSError("回收站操作已取消")
        raise OSError(f"无法移入回收站（错误码 {rc}）")
    raise OSError("当前系统无法移入回收站")


class FileRec:
    __slots__ = ("path", "name", "size", "mtime", "ctime")

    def __init__(self, path: str, name: str, size: int, mtime: float, ctime: float):
        self.path = path
        self.name = name
        self.size = int(size)
        self.mtime = float(mtime)
        self.ctime = float(ctime)


class ScanResult:
    __slots__ = (
        "root", "folder_count", "file_count", "total_size",
        "errors", "cancelled", "by_name", "ext_filter",
    )

    def __init__(self, root: str):
        self.root = root
        self.folder_count = 0
        self.file_count = 0
        self.total_size = 0
        self.errors = 0
        self.cancelled = False
        self.by_name = {}  # name_key -> list[FileRec]
        self.ext_filter = ()  # 规范化后缀元组；空 = 未过滤


def _newer_first(a: FileRec, b: FileRec) -> bool:
    """True if a 比 b 新（应保留 a）。日期相同则路径排序靠前的留下。"""
    if a.mtime != b.mtime:
        return a.mtime > b.mtime
    if a.ctime != b.ctime:
        return a.ctime > b.ctime
    return a.path.casefold() < b.path.casefold()


def plan_same_size_deletes(by_name: dict) -> list:
    """同名且大小相同：每组保留最新一份，其余列入待删。

    返回 [(keep: FileRec, victims: list[FileRec]), ...]
    """
    plans = []
    for key in sorted(by_name.keys()):
        items = by_name.get(key) or []
        if len(items) < 2:
            continue
        by_size = defaultdict(list)
        for rec in items:
            by_size[rec.size].append(rec)
        for _sz, group in sorted(by_size.items(), key=lambda x: -x[0]):
            if len(group) < 2:
                continue
            keep = group[0]
            for rec in group[1:]:
                if _newer_first(rec, keep):
                    keep = rec
            victims = [r for r in group if r.path != keep.path]
            if victims:
                plans.append((keep, victims))
    return plans


def _still_safe_to_delete(keep: FileRec, victim: FileRec) -> str:
    """删除前复查文件状态和实际内容。空串表示可删。"""
    try:
        if not os.path.isfile(keep.path):
            return "保留文件已不在"
        if not os.path.isfile(victim.path):
            return "目标已不在"
        ks = os.stat(keep.path)
        vs = os.stat(victim.path)
    except OSError as e:
        return str(e)
    if os.path.normcase(os.path.abspath(keep.path)) == os.path.normcase(os.path.abspath(victim.path)):
        return "与保留文件是同一路径"
    if _name_key(os.path.basename(keep.path)) != _name_key(os.path.basename(victim.path)):
        return "文件名已变"
    if int(ks.st_size) != int(vs.st_size):
        return "大小已不一致"
    if vs.st_mtime > ks.st_mtime + 1e-3:
        return "目标已比保留文件新"
    try:
        # 绕过 filecmp 的缓存，防止保留了时间戳的修改沿用旧比较结果。
        with open(keep.path, "rb") as first, open(victim.path, "rb") as second:
            while True:
                original = first.read(1024 * 1024)
                if original != second.read(1024 * 1024):
                    return "文件内容不同"
                if not original:
                    break
        for path, before in ((keep.path, ks), (victim.path, vs)):
            after = os.stat(path)
            if not os.path.samestat(before, after) or (before.st_size, before.st_mtime_ns) != (
                after.st_size, after.st_mtime_ns
            ):
                return "比较期间文件已变化"
    except OSError as e:
        return str(e)
    return ""


def _as_ext_filt(ext_filter):
    """None = 全部文件。False = 一个都不收。其它可迭代 = 白名单。"""
    if ext_filter is False:
        return False
    if not ext_filter:
        return None
    filt = {normalize_ext(x) for x in ext_filter}
    filt.discard("")
    if not filt:
        return False
    return filt


def _ext_blocked(filt, name: str) -> bool:
    if filt is False:
        return True
    if filt is None:
        return False
    return not file_matches_ext_filter(name, filt)


def scan_tree(root: str, progress_cb=None, should_stop=None, ext_filter=None) -> ScanResult:
    result = ScanResult(root)
    filt = _as_ext_filt(ext_filter)
    if isinstance(filt, set):
        result.ext_filter = tuple(sorted(filt))
    if not root or not os.path.isdir(root):
        result.errors += 1
        return result
    result.folder_count = 1
    by_name = defaultdict(list)
    stack = [root]
    last_emit = 0.0
    while stack:
        if should_stop and should_stop():
            result.cancelled = True
            break
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    if should_stop and should_stop():
                        result.cancelled = True
                        break
                    if _skip_entry(entry):
                        continue
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        result.errors += 1
                        continue
                    if is_dir:
                        result.folder_count += 1
                        stack.append(entry.path)
                        continue
                    try:
                        is_file = entry.is_file(follow_symlinks=False)
                    except OSError:
                        result.errors += 1
                        continue
                    if not is_file:
                        continue
                    if _ext_blocked(filt, entry.name):
                        continue
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        result.errors += 1
                        continue
                    rec = FileRec(
                        entry.path,
                        entry.name,
                        int(st.st_size),
                        float(st.st_mtime),
                        float(getattr(st, "st_ctime", st.st_mtime) or st.st_mtime),
                    )
                    by_name[_name_key(rec.name)].append(rec)
                    result.file_count += 1
                    result.total_size += rec.size
                    now = time.monotonic()
                    if progress_cb and (now - last_emit) >= 0.12:
                        last_emit = now
                        try:
                            progress_cb(result.file_count, result.folder_count, current)
                        except Exception:
                            pass
                if result.cancelled:
                    break
        except OSError:
            result.errors += 1
    result.by_name = dict(by_name)
    if progress_cb:
        try:
            progress_cb(result.file_count, result.folder_count, root)
        except Exception:
            pass
    return result


def build_report_text(result: ScanResult) -> tuple:
    """返回 (正文, 可删文件数, 可释放字节)。"""
    plans = plan_same_size_deletes(result.by_name)
    del_n = sum(len(v) for _k, v in plans)
    free_b = sum(r.size for _k, vs in plans for r in vs)

    same_name_groups = 0
    same_name_diff_size = 0
    for items in result.by_name.values():
        if len(items) < 2:
            continue
        same_name_groups += 1
        sizes = {r.size for r in items}
        if len(sizes) > 1:
            same_name_diff_size += 1

    lines = []
    a = lines.append
    a(f"目录    {result.root}")
    a(f"文件夹  {result.folder_count}")
    a(f"文件    {result.file_count}")
    a(f"总大小  {_fmt_size(result.total_size)}")
    filt = getattr(result, "ext_filter", ()) or ()
    if filt:
        shown = "、".join("." + e for e in filt)
        a(f"扩展名过滤  {shown}（其余未计入）")
    if result.errors:
        a(f"无法访问  {result.errors} 处（权限/已消失等，已跳过）")
    a("")
    a(f"同名文件组      {same_name_groups}")
    a(f"其中大小不一致  {same_name_diff_size} 组（不清除）")
    a(f"可去除          {del_n} 个较早版本，约 {_fmt_size(free_b)}")
    a("规则：文件名相同，且字节大小相同，才删除修改日期更早的那份。")
    a("")

    shown = 0
    # 先列可清除组，再列仅同名不同大小
    clearable_keys = set()
    for keep, victims in plans:
        key = _name_key(keep.name)
        clearable_keys.add(key)
        if shown >= _REPORT_GROUP_CAP:
            continue
        shown += 1
        a(f"━━ {keep.name}  · {_fmt_size(keep.size)} · {1 + len(victims)} 个，大小一致")
        a(f"  保留  {_fmt_time(keep.mtime)}  {keep.path}")
        for v in sorted(victims, key=lambda r: r.mtime):
            a(f"  清除  {_fmt_time(v.mtime)}  {v.path}")
        a("")

    for key in sorted(result.by_name.keys()):
        if key in clearable_keys:
            continue
        items = result.by_name[key]
        if len(items) < 2:
            continue
        if shown >= _REPORT_GROUP_CAP:
            continue
        shown += 1
        a(f"━━ {items[0].name}  · {len(items)} 个，大小不同（不清除）")
        for rec in sorted(items, key=lambda r: (-r.size, -r.mtime)):
            a(f"  {_fmt_size(rec.size):>10}  {_fmt_time(rec.mtime)}  {rec.path}")
        a("")

    leftover = same_name_groups - shown
    if leftover > 0:
        a(f"……还有 {leftover} 组未展开（报告最多列出 {_REPORT_GROUP_CAP} 组）")

    if same_name_groups == 0:
        a("没有发现同名文件。")

    return "\n".join(lines).rstrip() + "\n", del_n, free_b


def _reveal_in_explorer(path: str) -> None:
    """打开资源管理器并选中该文件，再把这个窗口带到最前。

    查重窗是模态的，explorer /select 常常开在它后面，文件也未必被选中。
    用 SHOpenFolderAndSelectItems 选中，稍后再把资源管理器窗口抬上来。
    """
    path = os.path.normpath(path or "")
    if not path:
        return
    target = path if os.path.exists(path) else os.path.dirname(path)
    if not target or not os.path.exists(target):
        return
    folder_name = os.path.basename(target if os.path.isdir(target) else os.path.dirname(target))
    try:
        _select_in_shell(target)
    except Exception:
        log.warning("定位文件失败 %s", path, exc_info=True)
        try:
            if os.path.isdir(target):
                subprocess.Popen(["explorer", target])
            else:
                subprocess.Popen(["explorer", "/select,", target])
        except Exception:
            log.warning("打开文件位置失败 %s", path)
            return
    QTimer.singleShot(200, lambda name=folder_name: _raise_explorer_window(name))


def _select_in_shell(path: str) -> None:
    """让资源管理器打开 path 所在文件夹并选中它。"""
    path = os.path.abspath(path)
    shell32 = ctypes.windll.shell32
    ole32 = ctypes.windll.ole32
    shell32.SHParseDisplayName.argtypes = [
        ctypes.c_wchar_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_ulong, ctypes.c_void_p,
    ]
    shell32.SHParseDisplayName.restype = ctypes.HRESULT
    shell32.ILFindLastID.argtypes = [ctypes.c_void_p]
    shell32.ILFindLastID.restype = ctypes.c_void_p
    shell32.ILClone.argtypes = [ctypes.c_void_p]
    shell32.ILClone.restype = ctypes.c_void_p
    shell32.ILRemoveLastID.argtypes = [ctypes.c_void_p]
    shell32.ILRemoveLastID.restype = ctypes.c_bool
    shell32.SHOpenFolderAndSelectItems.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p), ctypes.c_ulong,
    ]
    shell32.SHOpenFolderAndSelectItems.restype = ctypes.HRESULT
    shell32.ILFree.argtypes = [ctypes.c_void_p]

    hr_init = int(ole32.CoInitialize(None) or 0)
    pidl = ctypes.c_void_p()
    parent = ctypes.c_void_p()
    try:
        hr = int(shell32.SHParseDisplayName(path, None, ctypes.byref(pidl), 0, None) or 0)
        if hr < 0 or not pidl.value:
            raise OSError("SHParseDisplayName 失败")
        child = shell32.ILFindLastID(pidl)
        parent = ctypes.c_void_p(shell32.ILClone(pidl))
        if not parent.value or not shell32.ILRemoveLastID(parent):
            raise OSError("无法拆出父文件夹")
        items = (ctypes.c_void_p * 1)(child)
        hr = int(shell32.SHOpenFolderAndSelectItems(parent, 1, items, 0) or 0)
        if hr < 0:
            raise OSError("SHOpenFolderAndSelectItems 失败")
    finally:
        if parent.value:
            shell32.ILFree(parent)
        if pidl.value:
            shell32.ILFree(pidl)
        if hr_init == 0:
            ole32.CoUninitialize()


def _raise_explorer_window(folder_name: str) -> None:
    """把标题里含该文件夹名的资源管理器窗口抬到最前。"""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _enum(hwnd, _lparam):
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value != "CabinetWClass":
            return True
        if not folder_name:
            found.append(hwnd)
            return True
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, 512)
        if folder_name.casefold() in (title.value or "").casefold():
            found.append(hwnd)
        return True

    user32.EnumWindows(_enum, 0)
    if not found:
        return
    hwnd = found[-1]
    flags = 0x0001 | 0x0002 | 0x0040  # NOSIZE | NOMOVE | SHOWWINDOW
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetWindowPos.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
    ]
    user32.SetWindowPos.restype = ctypes.c_bool
    user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), 0, 0, 0, 0, flags)
    user32.SetWindowPos(hwnd, ctypes.c_void_p(-2), 0, 0, 0, 0, flags)
    user32.SetForegroundWindow(hwnd)


class _ScanWorker(QThread):
    progress = pyqtSignal(int, int, str)
    done = pyqtSignal(object)

    def __init__(self, root: str, parent=None, ext_filter=None):
        super().__init__(parent)
        self._root = root
        self._stop = False
        self._ext_filter = False if ext_filter is False else (tuple(ext_filter) if ext_filter else ())

    def stop(self):
        self._stop = True

    def run(self):
        def _prog(files, folders, cur):
            self.progress.emit(int(files), int(folders), str(cur or ""))

        try:
            result = scan_tree(
                self._root,
                progress_cb=_prog,
                should_stop=lambda: self._stop or self.isInterruptionRequested(),
                ext_filter=False if self._ext_filter is False else (self._ext_filter or None),
            )
        except Exception as e:
            log.exception("扫描失败")
            result = ScanResult(self._root)
            result.errors += 1
            result.cancelled = True
            result.by_name = {}
            log.error("扫描异常 %s", e)
        self.done.emit(result)


class _DeleteWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished_ok = pyqtSignal(int, int, list)

    def __init__(self, plans: list, parent=None):
        super().__init__(parent)
        self._plans = plans
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        ok = 0
        fail = 0
        errors = []
        victims = [(keep, v) for keep, vs in self._plans for v in vs]
        total = len(victims)
        for i, (keep, rec) in enumerate(victims, 1):
            if self._stop or self.isInterruptionRequested():
                break
            self.progress.emit(i, total, rec.path)
            reason = _still_safe_to_delete(keep, rec)
            if reason:
                fail += 1
                errors.append(f"{rec.path}  （{reason}）")
                continue
            try:
                _send_to_recycle(rec.path)
                ok += 1
            except Exception as e:
                fail += 1
                errors.append(f"{rec.path}  （{e}）")
                log.warning("删除失败 %s %s", rec.path, e)
        self.finished_ok.emit(ok, fail, errors)


class _ExtCard(QWidget):
    """扩展名过滤卡：彩色圆角。单击回填 / 圆点临时关闭 / ✕删除 / 按住拖动排序。"""

    clicked = pyqtSignal(str)
    remove_requested = pyqtSignal(str)
    live_toggled = pyqtSignal(str)

    def __init__(self, ext: str, accent: str, owner, parent=None):
        super().__init__(parent)
        self.ext = ext
        self._owner = owner
        self._press = None
        self._dragging = False
        self._live = True
        self.setObjectName("DupExtCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAutoFillBackground(False)
        self.setCursor(Qt.OpenHandCursor)
        self.setFixedHeight(_EXT_CARD_H)
        self.setMinimumWidth(_EXT_CARD_MIN_W)
        self.setToolTip(f".{ext} · 单击填回输入框，拖动可排序")

        self._accent = accent
        self._selected = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(1)
        self.btn_live = QPushButton("●", self)
        self.btn_live.setObjectName("DupExtCardLive")
        self.btn_live.setCursor(Qt.PointingHandCursor)
        self.btn_live.setFocusPolicy(Qt.NoFocus)
        self.btn_live.setFixedSize(14, 14)
        self.btn_live.clicked.connect(lambda: self.live_toggled.emit(self.ext))
        lay.addWidget(self.btn_live, 0, Qt.AlignVCenter)
        lab = QLabel("." + ext)
        lab.setAlignment(Qt.AlignCenter)
        lab.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lab.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(lab, 1)
        self._lab = lab

        self.btn_x = QPushButton("✕", self)
        self.btn_x.setObjectName("DupExtCardDel")
        self.btn_x.setCursor(Qt.PointingHandCursor)
        self.btn_x.setFocusPolicy(Qt.NoFocus)
        self.btn_x.setFixedSize(14, 14)
        self.btn_x.setToolTip("删除这个扩展名")
        self.btn_x.clicked.connect(lambda: self.remove_requested.emit(self.ext))
        lay.addWidget(self.btn_x, 0, Qt.AlignVCenter)
        self.set_live(True)

        controls_width = self.btn_live.width() + self.btn_x.width() + 10
        label_width = lab.fontMetrics().horizontalAdvance(lab.text())
        self.setFixedWidth(min(_EXT_CARD_MAX_W, max(_EXT_CARD_MIN_W, label_width + controls_width)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = max(0, self.width() - self.btn_live.width() - self.btn_x.width() - 10)
        self._lab.setText(self._lab.fontMetrics().elidedText("." + self.ext, Qt.ElideRight, width))

    def apply_theme(self):
        self._apply_card_style()

    def set_selected(self, selected: bool):
        selected = bool(selected)
        if selected == self._selected:
            return
        self._selected = selected
        self._apply_card_style()

    def set_live(self, on: bool):
        self._live = bool(on)
        self.btn_live.setText("●" if self._live else "○")
        self.btn_live.setToolTip(
            "关闭后查重会跳过它，下次打开程序仍保持" if self._live else "已关闭，再点一次打开"
        )
        font = self._lab.font()
        font.setStrikeOut(not self._live)
        self._lab.setFont(font)
        self._apply_card_style()

    def _apply_card_style(self):
        if not self._live:
            try:
                dark = bool(theme.is_dark)
            except Exception:
                dark = True
            accent = "#334155" if dark else "#e2e8f0"
            fg = "#94a3b8" if dark else "#64748b"
            bd = "#475569" if dark else "#cbd5e1"
            del_c = fg
            del_hover = "#ef4444"
        else:
            accent = self._accent
            fg = _ext_contrast_fg(accent)
            bd = "#ffffff" if self._selected else _mix_hex(accent, "#ffffff", 0.10)
            del_c = "rgba(255,255,255,200)"
            del_hover = "#111111"
        self.setStyleSheet(
            "#DupExtCard{background-color:%s;border:1px solid %s;"
            "border-radius:6px;}"
            "#DupExtCard QLabel{background:transparent;border:none;color:%s;"
            "font-size:11px;font-weight:600;}"
            "QPushButton#DupExtCardLive{background:transparent;border:none;"
            "color:%s;font-size:10px;font-weight:700;padding:0;}"
            "QPushButton#DupExtCardDel{background:transparent;border:none;"
            "color:%s;font-size:10px;font-weight:700;padding:0;}"
            "QPushButton#DupExtCardDel:hover{color:%s;}"
            % (accent, bd, fg, fg, del_c, del_hover)
        )

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.globalPos()
            self._dragging = False
        e.accept()

    def mouseMoveEvent(self, e):
        if self._press is None or not (e.buttons() & Qt.LeftButton):
            return
        if not self._dragging:
            if (e.globalPos() - self._press).manhattanLength() < _EXT_DRAG_PX:
                return
            self._dragging = True
            self.setCursor(Qt.ClosedHandCursor)
            self._owner._begin_ext_drag(self)
        self._owner._move_ext_drag(e.globalPos())

    def mouseReleaseEvent(self, e):
        was_drag = self._dragging
        if was_drag:
            self._owner._end_ext_drag(e.globalPos())
        self._dragging = False
        self._press = None
        self.setCursor(Qt.OpenHandCursor)
        if e.button() == Qt.LeftButton and not was_drag:
            if self.rect().contains(e.pos()):
                self.clicked.emit(self.ext)
        super().mouseReleaseEvent(e)


class _ExtEmptyCard(QWidget):
    """无过滤卡时的占位。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DupExtEmptyCard")
        self.setFixedSize(46, _EXT_CARD_H)
        self.setCursor(Qt.ArrowCursor)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        try:
            dark = bool(theme.is_dark)
        except Exception:
            dark = True
        rim = QColor("#4b5a85" if dark else "#c2cce0")
        dim = QColor("#6f7fa8" if dark else "#94a3b8")
        pen = QPen(rim, 1, Qt.CustomDashLine)
        pen.setDashPattern([3, 3])
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 6.0, 6.0)
        p.setPen(dim)
        f = QFont()
        f.setPixelSize(11)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, "未添加")
        p.end()


class _ExtDragPreview(QWidget):
    def __init__(self, pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self.setObjectName("DupExtDragPreview")
        self._pm = QPixmap(pixmap)
        self.setFixedSize(max(1, self._pm.width()), max(1, self._pm.height()))
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.hide()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setOpacity(0.55)
        p.drawPixmap(0, 0, self._pm)
        p.end()


class _ExtDropGuide(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DupExtDropGuide")
        self.setFixedSize(_EXT_GUIDE_W, _EXT_CARD_H)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.hide()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        try:
            dark = bool(theme.is_dark)
        except Exception:
            dark = True
        color = QColor("#ffffff" if dark else "#334155")
        pen = QPen(color, 2, Qt.CustomDashLine)
        pen.setDashPattern([3, 3])
        pen.setCapStyle(Qt.FlatCap)
        p.setPen(pen)
        x = self.width() // 2
        p.drawLine(x, 3, x, self.height() - 3)
        p.end()


class ExtFilterBar(QWidget):
    """扩展名过滤卡栏：输入 + 存卡 / 点选回填 / 圆点临时关闭 / ✕删除 / 拖拽排序。

    只处理仍开启的类型。
    圆点关掉的卡片仍留在栏上。标签顺序和开关都会写入用户设置。
    """

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DupExtFilterBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("#DupExtFilterBar{background:transparent;border:none;}")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._exts = []
        self._off = set()
        self._selected = None
        self._loading = False
        self._click_filter_on = False
        self._pfx_guide = None
        self._pfx_preview = None
        self._pfx_drag_ext = None
        self._pfx_grab = None
        self._pfx_drag_card = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.inp = QLineEdit()
        self.inp.setObjectName("CalcInput")
        self.inp.setPlaceholderText("查重类型，如 mp4")
        self.inp.setFixedHeight(MEDIUM_BUTTON_H)
        self.inp.setMinimumWidth(0)
        self.inp.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.inp.setClearButtonEnabled(True)
        self.inp.setCursor(Qt.IBeamCursor)
        self.inp.setToolTip(
            "输入扩展名后点 + 存成过滤卡，查重至少需要一张类型卡。\n"
            "可一次输入多个，用空格或逗号分开。\n"
            "卡片左侧圆点可关闭该扩展名，开关会记住。✕ 才是删除。"
        )
        self.inp.returnPressed.connect(self._save_current)
        row.addWidget(self.inp, 1)
        self.btn_add = apply_medium_button(QPushButton("+"))
        self.btn_add.setFixedWidth(36)
        self.btn_add.setMinimumHeight(28)
        self.btn_add.setCursor(Qt.PointingHandCursor)
        self.btn_add.setToolTip("把当前扩展名存成一张过滤卡")
        self.btn_add.clicked.connect(self._save_current)
        row.addWidget(self.btn_add, 0)
        lay.addLayout(row)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("DupExtScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.scroll.setMinimumWidth(0)
        self.scroll.setMinimumHeight(_EXT_CARD_H)
        self.scroll.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.scroll.setAttribute(Qt.WA_StyledBackground, True)
        self.scroll.setStyleSheet(
            "#DupExtScroll{background:transparent;border:none;}"
            "#DupExtScroll > QWidget > QWidget{background:transparent;}"
        )
        try:
            self.scroll.viewport().setAutoFillBackground(False)
            self.scroll.viewport().setStyleSheet("background:transparent;")
        except Exception:
            pass
        self.host = FlowLayoutHost()
        self.host.setObjectName("DupExtHost")
        self.host.setAttribute(Qt.WA_StyledBackground, True)
        self.host.setStyleSheet("#DupExtHost{background:transparent;border:none;}")
        self.flow = FlowLayout(self.host, h_spacing=_EXT_SPACING, v_spacing=_EXT_SPACING)
        self.scroll.setWidget(self.host)
        self._pfx_guide = _ExtDropGuide(self.host)
        lay.addWidget(self.scroll, 1)

        self._refresh_chips()
        self._install_blank_click_filter()
        try:
            theme.changed.connect(self.refresh_theme)
        except Exception:
            pass

    def exts(self) -> list:
        """全部卡片，含临时关掉的。用于保存。"""
        return list(self._exts)

    def active_exts(self) -> list:
        """当前参与查重的扩展名。"""
        return [e for e in self._exts if e not in self._off]

    def off_exts(self) -> list:
        """已关闭的扩展名，顺序与卡片一致。用于写入用户设置。"""
        return [e for e in self._exts if e in self._off]

    def set_exts(self, items, off=None):
        hist = []
        for s in items or []:
            e = normalize_ext(s)
            if e and e not in hist:
                hist.append(e)
                if len(hist) >= _EXT_MAX:
                    break
        if off is None:
            new_off = {e for e in self._off if e in hist}
        else:
            new_off = set()
            for s in off or []:
                e = normalize_ext(str(s))
                if e and e in hist:
                    new_off.add(e)
        if hist == self._exts and new_off == self._off:
            return
        self._loading = True
        try:
            self._exts = hist
            self._off = new_off
            if self._selected and self._selected not in self._exts:
                self._selected = None
            self._refresh_chips()
        finally:
            self._loading = False

    def refresh_theme(self, *_):
        apply_medium_button(self.btn_add)
        self.btn_add.setFixedWidth(36)
        self.btn_add.setMinimumHeight(28)
        for w in self._cards():
            w.apply_theme()
        empty = self.host.findChild(QWidget, "DupExtEmptyCard")
        if empty is not None:
            empty.update()
        g = getattr(self, "_pfx_guide", None)
        if g is not None:
            g.update()

    def _emit_changed(self):
        if self._loading:
            return
        self.changed.emit()

    def _save_current(self):
        added = parse_ext_input(self.inp.text())
        if not added:
            self.inp.setFocus()
            return
        grew = False
        for e in added:
            if e in self._exts:
                continue
            self._exts.append(e)
            grew = True
        if len(self._exts) > _EXT_MAX:
            del self._exts[: len(self._exts) - _EXT_MAX]
        self.inp.clear()
        if added:
            self._selected = added[-1]
        self._refresh_chips()
        if grew:
            self._emit_changed()

    def _use_ext(self, ext: str):
        self.inp.setText(ext)
        self.inp.setFocus()
        self._select_ext(ext)

    def _cards(self):
        return [w for w in self.host.children() if isinstance(w, _ExtCard)]

    def _select_ext(self, ext: str):
        self._selected = ext or None
        self._sync_card_selection()

    def _clear_selection(self):
        if not self._selected:
            return
        self._selected = None
        self._sync_card_selection()

    def _sync_card_selection(self):
        sel = self._selected
        for w in self._cards():
            w.set_selected(w.ext == sel)

    def _click_keeps_sel(self, w) -> bool:
        cur = w
        while cur is not None:
            if isinstance(cur, _ExtCard):
                return True
            if cur is self:
                break
            cur = cur.parentWidget()
        return False

    def _install_blank_click_filter(self):
        app = QApplication.instance()
        if app is None:
            return
        app.installEventFilter(self)
        self._click_filter_on = True
        try:
            self.destroyed.connect(self._teardown_click_filter)
        except Exception:
            pass

    def _teardown_click_filter(self, *_):
        if not getattr(self, "_click_filter_on", False):
            return
        app = QApplication.instance()
        if app is not None:
            try:
                app.removeEventFilter(self)
            except Exception:
                pass
        self._click_filter_on = False

    def eventFilter(self, obj, ev):
        try:
            if (
                ev.type() == QEvent.MouseButtonPress
                and ev.button() == Qt.LeftButton
                and self._selected
                and self.isVisible()
                and not getattr(self, "_pfx_drag_ext", None)
            ):
                w = None
                try:
                    w = QApplication.widgetAt(ev.globalPos())
                except Exception:
                    w = obj if isinstance(obj, QWidget) else None
                if w is not None and not self._click_keeps_sel(w):
                    self._clear_selection()
        except Exception:
            pass
        return super().eventFilter(obj, ev)

    def _toggle_ext(self, ext: str):
        if ext not in self._exts:
            return
        if ext in self._off:
            self._off.discard(ext)
        else:
            self._off.add(ext)
        for w in self._cards():
            if w.ext == ext:
                w.set_live(ext not in self._off)
                break
        self._emit_changed()

    def _remove_ext(self, ext: str):
        if ext not in self._exts:
            return
        if self._selected == ext:
            self._selected = None
        self._off.discard(ext)
        self._exts.remove(ext)
        self._refresh_chips()
        self._emit_changed()

    def _refresh_chips(self):
        self._pfx_clear_drag_visual()
        flow = self.flow
        while flow.count():
            it = flow.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        if not self._exts:
            empty = _ExtEmptyCard(self.host)
            flow.addWidget(empty)
            self._selected = None
        for i, ext in enumerate(self._exts):
            text = str(ext)
            accent = _EXT_PALETTE[i % len(_EXT_PALETTE)]
            card = _ExtCard(text, accent, self, self.host)
            card.set_live(text not in self._off)
            card.clicked.connect(lambda e=text: self._use_ext(e))
            card.live_toggled.connect(lambda e=text: self._toggle_ext(e))
            card.remove_requested.connect(lambda e=text: self._remove_ext(e))
            flow.addWidget(card)
        flow.invalidate()
        self.host.updateGeometry()
        if self._selected and self._selected not in self._exts:
            self._selected = None
        self._sync_card_selection()

    def _pfx_clear_drag_visual(self):
        g = getattr(self, "_pfx_guide", None)
        if g is not None:
            g.hide()
        pv = getattr(self, "_pfx_preview", None)
        if pv is not None:
            pv.hide()
            pv.setParent(None)
            pv.deleteLater()
        self._pfx_preview = None
        self._pfx_drag_ext = None
        self._pfx_grab = None
        self._pfx_drag_card = None

    def _begin_ext_drag(self, card):
        self._pfx_clear_drag_visual()
        ext = str(card.ext or "")
        if ext not in self._exts:
            return
        self._pfx_drag_ext = ext
        self._pfx_drag_card = card
        try:
            card.grabMouse()
        except Exception:
            pass
        pm = card.grab()
        pv = _ExtDragPreview(pm, self)
        self._pfx_preview = pv
        try:
            pos = card.mapTo(self, QPoint(0, 0))
        except Exception:
            pos = QPoint(0, 0)
        pv.move(pos)
        pv.show()
        pv.raise_()
        grab = QPoint(pm.width() // 2, pm.height() // 2)
        self._pfx_grab = grab
        press = getattr(card, "_press", None)
        if press is not None:
            self._pfx_move_visual(press)

    def _move_ext_drag(self, gpos):
        ext = self._pfx_drag_ext
        if not ext:
            return
        pv = self._pfx_preview
        if pv is not None:
            grab = self._pfx_grab or QPoint(pv.width() // 2, pv.height() // 2)
            pv.move(self.mapFromGlobal(gpos) - grab)
        self._pfx_move_visual(gpos)

    def _pfx_drop_location(self, gpos):
        viewport = self.scroll.viewport()
        if not viewport.rect().contains(viewport.mapFromGlobal(gpos)):
            return None
        return self.flow.drop_location(self.host.mapFromGlobal(gpos))

    def _pfx_move_visual(self, gpos):
        location = self._pfx_drop_location(gpos)
        guide = self._pfx_guide
        if location is None:
            guide.hide()
            return
        _, position = location
        x = max(0, min(position.x() - _EXT_GUIDE_W // 2, self.host.width() - _EXT_GUIDE_W))
        guide.move(x, position.y())
        guide.show()
        guide.raise_()

    def _end_ext_drag(self, gpos):
        ext = self._pfx_drag_ext
        card = self._pfx_drag_card
        if card is not None:
            try:
                card.releaseMouse()
            except Exception:
                pass
            card.setCursor(Qt.OpenHandCursor)
        location = self._pfx_drop_location(gpos)
        if ext and location is not None:
            self._commit_ext_order(ext, location[0])
        self._pfx_clear_drag_visual()

    def _commit_ext_order(self, ext: str, insert: int):
        if ext not in self._exts:
            return
        old = self._exts.index(ext)
        if old == insert or old == insert - 1:
            self._refresh_chips()
            return
        self._exts.pop(old)
        if old < insert:
            insert -= 1
        insert = max(0, min(insert, len(self._exts)))
        self._exts.insert(insert, ext)
        self._refresh_chips()
        self._emit_changed()


class _ReportDialog(QDialog):
    def __init__(self, parent, result: ScanResult):
        super().__init__(parent)
        self.setWindowTitle("目录统计")
        self.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.setMinimumSize(560, 400)
        self.resize(720, 520)
        self.setModal(True)
        self._result = result
        self._del_worker = None
        self._build()
        self._fill()
        try:
            theme.changed.connect(self._restyle)
        except Exception:
            pass

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        self.body = QScrollArea()
        self.body.setObjectName("DupReportScroll")
        self.body.setWidgetResizable(True)
        self.body.setFrameShape(QFrame.NoFrame)
        self.body.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._host = QWidget()
        self._host.setObjectName("DupReportHost")
        self._host_lay = QVBoxLayout(self._host)
        self._host_lay.setContentsMargins(8, 8, 8, 8)
        self._host_lay.setSpacing(2)
        self._host_lay.addStretch(1)
        self.body.setWidget(self._host)
        root.addWidget(self.body, 1)

        self.lbl_status = QLabel("")
        self.lbl_status.setProperty("typo", "muted")
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status, 0)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.btn_clean = QPushButton("删除同名文件并关闭")
        apply_btn_download(self.btn_clean)
        self.btn_clean.setCursor(Qt.PointingHandCursor)
        self.btn_clean.clicked.connect(self._on_clean)
        self.btn_close = apply_medium_button(QPushButton("关闭"))
        self.btn_close.clicked.connect(self.close)
        row.addWidget(self.btn_clean, 0)
        row.addStretch(1)
        row.addWidget(self.btn_close, 0)
        root.addLayout(row)
        self._restyle()

    def _restyle(self, *_):
        bg = tk("panel")
        text = tk("text")
        mut = tk("text_mut")
        self.setStyleSheet(
            f"QDialog{{background:{bg};color:{text};}}"
            f"QScrollArea#DupReportScroll{{background:{tk('input_bg')};"
            f"border:1px solid {tk('border')};border-radius:8px;}}"
            f"QWidget#DupReportHost{{background:transparent;}}"
            f"QLabel{{background:transparent;}}"
        )
        self.lbl_status.setStyleSheet(f"color:{mut};background:transparent;")
        apply_btn_download(self.btn_clean)
        apply_medium_button(self.btn_close)
        if getattr(self, "_filled", False):
            self._fill()

    def _clear_host(self):
        lay = self._host_lay
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _line(self, text: str, color: str = None) -> QLabel:
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        c = color or tk("text")
        lab.setStyleSheet(
            f"color:{c};background:transparent;"
            "font-family:'Microsoft YaHei','Segoe UI';font-size:14px;"
        )
        return lab

    def _fill(self):
        self._filled = True
        self._clear_host()
        result = self._result
        text, del_n, free_b = build_report_text(result)
        self._del_n = del_n
        self._free_b = free_b

        # 摘要：到规则行为止（不含分组明细）
        summary_lines = []
        for line in (text or "").splitlines():
            if line.startswith("━━"):
                break
            summary_lines.append(line)
        for line in summary_lines:
            self._host_lay.addWidget(self._line(line if line else " "))

        plans = plan_same_size_deletes(result.by_name)
        shown = 0
        clearable_keys = set()
        for keep, victims in plans:
            key = _name_key(keep.name)
            clearable_keys.add(key)
            if shown >= _REPORT_GROUP_CAP:
                continue
            shown += 1
            self._host_lay.addWidget(self._line(
                f"━━ {keep.name}  · {_fmt_size(keep.size)} · {1 + len(victims)} 个，大小一致"
            ))
            self._host_lay.addWidget(self._line(
                f"  保留  {_fmt_time(keep.mtime)}  {keep.path}", "#22c55e"
            ))
            for v in sorted(victims, key=lambda r: r.mtime):
                self._host_lay.addWidget(self._line(
                    f"  清除  {_fmt_time(v.mtime)}  {v.path}", "#ef4444"
                ))
            self._host_lay.addWidget(self._line(" "))

        for key in sorted(result.by_name.keys()):
            if key in clearable_keys:
                continue
            items = result.by_name[key]
            if len(items) < 2:
                continue
            if shown >= _REPORT_GROUP_CAP:
                continue
            shown += 1
            self._host_lay.addWidget(self._line(
                f"━━ {items[0].name}  · {len(items)} 个，大小不同（不清除）"
            ))
            for rec in sorted(items, key=lambda r: (-r.size, -r.mtime)):
                row_w = QWidget()
                row_w.setStyleSheet("background:transparent;")
                hl = QHBoxLayout(row_w)
                hl.setContentsMargins(0, 0, 0, 0)
                hl.setSpacing(6)
                btn = apply_mini_button(QPushButton("打开"))
                btn.setCursor(Qt.PointingHandCursor)
                btn.setToolTip("打开文件所在位置")
                btn.clicked.connect(lambda _=False, p=rec.path: _reveal_in_explorer(p))
                hl.addWidget(btn, 0, Qt.AlignTop)
                hl.addWidget(self._line(
                    f"{_fmt_size(rec.size)}  {_fmt_time(rec.mtime)}  {rec.path}"
                ), 1)
                self._host_lay.addWidget(row_w)
            self._host_lay.addWidget(self._line(" "))

        same_name_groups = sum(
            1 for items in result.by_name.values() if len(items) >= 2
        )
        leftover = same_name_groups - shown
        if leftover > 0:
            self._host_lay.addWidget(self._line(
                f"……还有 {leftover} 组未展开（报告最多列出 {_REPORT_GROUP_CAP} 组）"
            ))

        self._host_lay.addStretch(1)

        if del_n:
            self.lbl_status.setText(
                f"可去除 {del_n} 个较早版本，约 {_fmt_size(free_b)}。"
                "仅处理「同名且大小相同」的文件，不同大小的同名文件不会动。"
            )
            self.btn_clean.setEnabled(True)
        else:
            self.lbl_status.setText("没有「同名且大小相同」的文件，无需清除。")
            self.btn_clean.setEnabled(False)

    def _on_clean(self):
        plans = plan_same_size_deletes(self._result.by_name)
        victims = [(keep, v) for keep, vs in plans for v in vs]
        if not victims:
            self.btn_clean.setEnabled(False)
            self.close()
            return
        self.btn_clean.setEnabled(False)
        self.btn_close.setEnabled(False)
        self.lbl_status.setText("正在移入回收站…")
        self._del_worker = _DeleteWorker(plans, self)
        self._del_worker.progress.connect(self._on_del_progress)
        self._del_worker.finished_ok.connect(self._on_del_done)
        self._del_worker.start()

    def _on_del_progress(self, done: int, total: int, path: str):
        name = os.path.basename(path) if path else ""
        self.lbl_status.setText(f"正在移入回收站… {done}/{total}  {name}")

    def _on_del_done(self, ok: int, fail: int, errors: list):
        log.info(
            "同名清理完成 ok=%s fail=%s err=%s root=%s",
            ok, fail, len(errors or []), self._result.root,
        )
        self.close()

    def closeEvent(self, event):
        w = self._del_worker
        if w is not None and w.isRunning():
            event.ignore()
            return
        super().closeEvent(event)

    def shutdown(self):
        stop_qthread(self._del_worker, name="dup_clean_delete")


def _pack_file(rec: FileRec) -> dict:
    return {
        "path": rec.path,
        "name": rec.name,
        "size": int(rec.size),
        "mtime": float(rec.mtime),
    }


def name_group_title(display_name: str, count: int, same_size: bool, size: int) -> str:
    if same_size:
        return f"同名 · {display_name} · {count} 个 · 大小一致 {_fmt_size(size)}"
    return f"同名 · {display_name} · {count} 个 · 大小不同"


def suspect_group_title(size: int, count: int) -> str:
    return f"疑似同一文件 · {_fmt_size(size)} · {count} 个 · 名字不同"


def prepare_roots(paths) -> list:
    """去重，并丢掉已经落在另一个所选目录里面的路径。"""
    items = []
    seen = set()
    for raw in paths or []:
        p = (raw or "").strip()
        if not p or not os.path.isdir(p):
            continue
        abs_p = os.path.abspath(p)
        key = os.path.normcase(abs_p)
        if key in seen:
            continue
        seen.add(key)
        items.append(abs_p)
    items.sort(key=lambda p: (len(os.path.normcase(p)), os.path.normcase(p)))
    kept = []
    kept_keys = []
    for p in items:
        key = os.path.normcase(p)
        nested = False
        for parent in kept_keys:
            if key == parent or key.startswith(parent + os.sep):
                nested = True
                break
        if nested:
            continue
        kept.append(p)
        kept_keys.append(key)
    return kept


class MultiIndex:
    """跨目录查重的内存索引。同名凑满 2 个就出组；不同名且大小相同（不含 0 字节）出疑似组。"""

    def __init__(self):
        self.by_name = defaultdict(list)
        self.by_size = defaultdict(list)
        self._name_live = set()
        self._size_live = set()
        self.name_groups = 0
        self.suspect_groups = 0

    def add(self, rec: FileRec) -> list:
        events = []
        nk = _name_key(rec.name)
        named = self.by_name[nk]
        named.append(rec)
        if len(named) >= 2:
            first = nk not in self._name_live
            if first:
                self._name_live.add(nk)
                self.name_groups += 1
                chunk = list(named)
            else:
                chunk = [rec]
            sizes = {int(r.size) for r in named}
            same = len(sizes) == 1
            events.append({
                "kind": "name",
                "key": "n:" + nk,
                "title": name_group_title(named[0].name, len(named), same, int(named[0].size)),
                "files": [_pack_file(r) for r in chunk],
            })
        if int(rec.size) > 0:
            sized = self.by_size[int(rec.size)]
            sized.append(rec)
            names = {_name_key(r.name) for r in sized}
            if len(names) >= 2:
                first = int(rec.size) not in self._size_live
                if first:
                    self._size_live.add(int(rec.size))
                    self.suspect_groups += 1
                    chunk = list(sized)
                else:
                    chunk = [rec]
                events.append({
                    "kind": "suspect",
                    "key": "s:" + str(int(rec.size)),
                    "title": suspect_group_title(int(rec.size), len(sized)),
                    "files": [_pack_file(r) for r in chunk],
                })
        return events


class _SequenceIndex:
    """按实际目录和文件名前缀收集编号，连续缺号用范围表示。"""

    _number = re.compile(r"[0-9]+$")
    _report_name = "_缺失文件清单.txt"

    def __init__(self, ignore_ext):
        self._ignore_ext = ignore_ext
        self._groups = defaultdict(lambda: defaultdict(list))
        self._reports = {}

    def add(self, path, name):
        folder = os.path.dirname(path)
        stem, ext = os.path.splitext(name)
        ext = ext.lower()
        if ext == ".txt" and ("缺失" in stem or "清单" in stem):
            if name == self._report_name or folder not in self._reports:
                self._reports[folder] = path
        if ext in (".tmp", ".part"):
            return
        number = self._number.search(stem)
        if number is None:
            return
        prefix = stem[:number.start()]
        digits = number.group()
        key = prefix.casefold(), "" if self._ignore_ext else ext
        self._groups[folder][key].append((int(digits), len(digits), prefix))

    @staticmethod
    def _looks_like_date(number):
        year, month_day = divmod(number, 10000)
        if not 1900 <= year <= 2099:
            return False
        month, day = divmod(month_day, 100)
        try:
            datetime(year, month, day)
        except ValueError:
            return False
        return True

    def results(self, should_stop):
        results = []
        for folder, groups in sorted(self._groups.items(), key=lambda item: os.path.normcase(item[0])):
            if should_stop():
                break
            report = self._reports.get(folder)
            expected = 0
            if report and any(not prefix.strip() for prefix, _ in groups):
                try:
                    from pages.page_gallery import parse_missing_report
                    expected = int(parse_missing_report(report).get("expected_count") or 0)
                except (ImportError, OSError, ValueError):
                    log.debug("无法读取序列清单 %s", report, exc_info=True)
            count = 0
            labels = []
            fill_ranges = []
            for (prefix_key, ext), items in sorted(groups.items()):
                if should_stop():
                    return results
                numbers = sorted({number for number, _, _ in items})
                width = max(digits for _, digits, _ in items)
                prefix = items[0][2]
                gallery_sequence = not prefix_key.strip()
                start, end = numbers[0], numbers[-1]
                if gallery_sequence and expected > 0:
                    start, end = 1, max(end, expected)
                    width = max(width, 4, len(str(end)))
                elif len(numbers) < 2 or (width == 8 and all(map(self._looks_like_date, numbers))):
                    continue

                previous = start - 1
                numbers.append(end + 1)
                for number in numbers:
                    if number > previous + 1:
                        first, last = previous + 1, number - 1
                        count += last - first + 1
                        if len(labels) < 9:
                            head = f"{prefix}{first:0{width}d}{ext}"
                            tail = f"{prefix}{last:0{width}d}{ext}"
                            labels.append(head if first == last else f"{head}–{tail}")
                        if gallery_sequence:
                            fill_ranges.append((first, last, width))
                    previous = max(previous, number)
            if count:
                results.append({
                    "folder": folder, "total_missing": count, "labels": labels,
                    "fill_ranges": fill_ranges,
                })
        return results


def _ext_filt(ext_filter):
    return _as_ext_filt(ext_filter)


def walk_files(roots, filt, should_stop, visitor):
    """遍历这些根目录下的普通文件。返回 (文件数, 文件夹数, 错误数, 是否取消)。"""
    files = 0
    folders = 0
    errors = 0
    seen_dir = set()
    for root in roots:
        if should_stop and should_stop():
            return files, folders, errors, True
        stack = [root]
        while stack:
            if should_stop and should_stop():
                return files, folders, errors, True
            current = stack.pop()
            key = os.path.normcase(os.path.abspath(current))
            if key in seen_dir:
                continue
            seen_dir.add(key)
            folders += 1
            try:
                iterator = os.scandir(current)
            except OSError:
                errors += 1
                continue
            with iterator:
                for entry in iterator:
                    if should_stop and should_stop():
                        return files, folders, errors, True
                    if _skip_entry(entry):
                        continue
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        errors += 1
                        continue
                    if is_dir:
                        stack.append(entry.path)
                        continue
                    try:
                        if not entry.is_file(follow_symlinks=False):
                            continue
                    except OSError:
                        errors += 1
                        continue
                    if _ext_blocked(filt, entry.name):
                        continue
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        errors += 1
                        continue
                    files += 1
                    if visitor is not None:
                        visitor(entry.path, entry.name, st)
    return files, folders, errors, False


class _MultiScanWorker(QThread):
    progress = pyqtSignal(int, str)
    group = pyqtSignal(object)
    finished_scan = pyqtSignal(object)

    def __init__(self, roots, ext_filter=None, parent=None, *, sequence_ignore_ext=None):
        super().__init__(parent)
        self._roots = list(roots or [])
        self._ext_filter = False if ext_filter is False else (tuple(ext_filter) if ext_filter else ())
        self._sequence_ignore_ext = sequence_ignore_ext
        self._stop = False

    def stop(self):
        self._stop = True

    def _halt(self) -> bool:
        return bool(self._stop or self.isInterruptionRequested())

    def run(self):
        try:
            self._run()
        except Exception as e:
            log.exception("文件检查失败")
            self.finished_scan.emit({
                "cancelled": True,
                "error": str(e),
                "files": 0,
                "errors": 1,
                "name_groups": 0,
                "suspect_groups": 0,
            })

    def _run(self):
        roots = prepare_roots(self._roots)
        if not roots:
            self.finished_scan.emit({
                "cancelled": False,
                "empty": True,
                "files": 0,
                "errors": 0,
                "name_groups": 0,
                "suspect_groups": 0,
            })
            return

        sequences = None if self._sequence_ignore_ext is None else _SequenceIndex(self._sequence_ignore_ext)
        index = MultiIndex() if sequences is None else None
        checked = 0
        last = 0.0

        def _on_file(path, name, st):
            nonlocal checked, last
            checked += 1
            if sequences is not None:
                sequences.add(path, name)
            else:
                rec = FileRec(
                    path,
                    name,
                    int(st.st_size),
                    float(st.st_mtime),
                    float(getattr(st, "st_ctime", st.st_mtime) or st.st_mtime),
                )
                for ev in index.add(rec):
                    self.group.emit(ev)
            now = time.monotonic()
            if now - last >= 0.12:
                last = now
                self.progress.emit(checked, path)

        scan_filter = _ext_filt(self._ext_filter) if sequences is None else None
        _files, _folders, errors, cancelled = walk_files(roots, scan_filter, self._halt, _on_file)
        gaps = sequences.results(self._halt) if sequences is not None and not cancelled else []
        cancelled = cancelled or self._halt()
        self.finished_scan.emit({
            "cancelled": cancelled,
            "files": checked,
            "errors": errors,
            "name_groups": index.name_groups if index is not None else 0,
            "suspect_groups": index.suspect_groups if index is not None else 0,
            "sequence_gaps": [] if cancelled else gaps,
        })


class _DupGroupBlock(QFrame):
    def __init__(self, kind: str, parent=None, *, scanning=False):
        super().__init__(parent)
        self.setObjectName("FileCheckResult")
        self.kind = kind
        self._scanning = scanning
        self._recycle_buttons = []
        self._paths = set()
        self._files = []
        self._row_widgets = {}
        self._capped = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        self.header = QLabel("")
        self.header.setObjectName("FileCheckGroup")
        self.header.setTextFormat(Qt.PlainText)
        self.header.setWordWrap(True)
        self.header.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.header.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.header)
        self._rows = QVBoxLayout()
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(5)
        lay.addLayout(self._rows)

    def set_title(self, text: str):
        self.header.setText(text or "")

    def file_count(self) -> int:
        return len(self._files)

    def _refresh_title(self):
        files = self._files
        if not files:
            return
        if self.kind == "suspect":
            self.set_title(suspect_group_title(int(files[0].get("size") or 0), len(files)))
            return
        sizes = {int(f.get("size") or 0) for f in files}
        same = len(sizes) == 1
        size = next(iter(sizes)) if same else 0
        self.set_title(name_group_title(files[0].get("name") or "", len(files), same, size))

    def add_files(self, files) -> bool:
        added = False
        for info in files or []:
            path = (info or {}).get("path") or ""
            if not path or path in self._paths:
                continue
            if len(self._paths) >= _MULTI_ROW_CAP:
                if not self._capped:
                    self._capped = True
                    note = QLabel(f"这一组只列出前 {_MULTI_ROW_CAP} 个")
                    note.setObjectName("FileCheckMuted")
                    self._rows.addWidget(note)
                continue
            self._paths.add(path)
            self._files.append(dict(info))
            row = self._file_row(info)
            self._row_widgets[path] = row
            self._rows.addWidget(row)
            added = True
        if added:
            self._refresh_title()
        return added

    def drop_path(self, path: str) -> int:
        path = path or ""
        self._files = [f for f in self._files if (f.get("path") or "") != path]
        self._paths.discard(path)
        row = self._row_widgets.pop(path, None)
        if row is not None:
            self._rows.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        if self._files:
            self._refresh_title()
        return len(self._files)

    def _file_row(self, info: dict) -> QWidget:
        path = info.get("path") or ""
        row = QFrame(self)
        row.setObjectName("FileCheckFileRow")
        hl = QHBoxLayout(row)
        hl.setContentsMargins(8, 6, 8, 6)
        hl.setSpacing(8)

        details = QVBoxLayout()
        details.setSpacing(2)
        location = QLabel(path)
        location.setObjectName("FileCheckPath")
        location.setTextFormat(Qt.PlainText)
        location.setWordWrap(True)
        location.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        location.setTextInteractionFlags(Qt.TextSelectableByMouse)
        location.setToolTip(path)
        details.addWidget(location)
        metadata = QLabel(f"{_fmt_size(info.get('size') or 0)}  ·  {_fmt_time(info.get('mtime') or 0)}")
        metadata.setObjectName("FileCheckMuted")
        metadata.setTextInteractionFlags(Qt.TextSelectableByMouse)
        details.addWidget(metadata)
        hl.addLayout(details, 1)

        btn = apply_mini_button(QPushButton("定位"))
        btn.setCursor(Qt.PointingHandCursor)
        btn.setAutoDefault(False)
        btn.setToolTip("在资源管理器中定位到这个文件，并把该窗口带到最前")
        btn.clicked.connect(lambda _=False, p=path: _reveal_in_explorer(p))
        hl.addWidget(btn, 0, Qt.AlignVCenter)
        btn_del = QPushButton("×")
        btn_del.setObjectName("FileCheckRecycle")
        btn_del.setCursor(Qt.PointingHandCursor)
        btn_del.setAutoDefault(False)
        btn_del.setFixedSize(24, 24)
        btn_del.setAccessibleName("移入回收站")
        btn_del.setToolTip("把这个文件移入回收站")
        btn_del.setEnabled(not self._scanning)
        if self._scanning:
            self._recycle_buttons.append(btn_del)
        btn_del.clicked.connect(lambda _=False, p=path: self._ask_delete(p))
        hl.addWidget(btn_del, 0, Qt.AlignVCenter)
        return row

    def _ask_delete(self, path: str):
        host = self.parent()
        while host is not None and not isinstance(host, MultiDupDialog):
            host = host.parent()
        if host is not None:
            host.delete_listed_file(path)

    def finish_scan(self):
        self._scanning = False
        for button in self._recycle_buttons:
            button.setEnabled(True)
        self._recycle_buttons.clear()


class MultiDupDialog(QDialog):
    """按当前模式展示查重或缺号结果。"""

    def __init__(self, parent, roots, ext_filter=None, *, sequence_ignore_ext=None):
        super().__init__(parent)
        self._is_sequence = sequence_ignore_ext is not None
        self.setWindowTitle("序列检查" if self._is_sequence else "文件查重")
        self.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.setMinimumWidth(520)
        self.setModal(True)
        self._roots = list(roots or [])
        self._ext_filter = False if ext_filter is False else list(ext_filter or [])
        self._sequence_ignore_ext = sequence_ignore_ext
        self._worker = None
        self._blocks = {}
        self._duplicate_counts = {"name": 0, "suspect": 0}
        self._deleted = set()
        self._closing = False
        self._running = False
        self.fill_request = None
        self.scan_summary = ""
        self._build()
        self._compact_height = self.sizeHint().height()
        self.resize(648, self._compact_height)
        self._results_resize = QTimer(self)
        self._results_resize.setSingleShot(True)
        self._results_resize.timeout.connect(self._fit_results)
        try:
            theme.changed.connect(self._restyle)
        except Exception:
            pass

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(12)
        self._build_summary(root)

        self.body = QScrollArea()
        self.body.setObjectName("FileCheckScroll")
        self.body.setWidgetResizable(True)
        self.body.setFrameShape(QFrame.NoFrame)
        self.body.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._host = QWidget()
        self._host.setObjectName("FileCheckHost")
        self._host_lay = QVBoxLayout(self._host)
        self._host_lay.setContentsMargins(0, 0, 0, 0)
        self._host_lay.setSpacing(8)
        self._host_lay.setSizeConstraint(QLayout.SetMinimumSize)
        heading = QLabel("缺号明细" if self._is_sequence else "查重明细")
        heading.setObjectName("FileCheckSection")
        self._host_lay.addWidget(heading)
        self._host_lay.addStretch(1)
        self.body.setWidget(self._host)
        root.addWidget(self.body, 1)
        self.body.hide()
        self.body.viewport().setObjectName("FileCheckViewport")
        self.body.viewport().setAttribute(Qt.WA_StyledBackground, True)
        self._host.setAttribute(Qt.WA_StyledBackground, True)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.btn_cancel = apply_medium_button(QPushButton("取消"))
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_close = apply_medium_button(QPushButton("关闭"))
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.clicked.connect(self.close)
        scope = QLabel(f"检查范围  ·  {len(self._roots):,} 个所选目录")
        scope.setObjectName("FileCheckMuted")
        scope.setToolTip("\n".join(map(str, self._roots)))
        row.addWidget(scope)
        row.addStretch(1)
        row.addWidget(self.btn_cancel, 0)
        row.addWidget(self.btn_close, 0)
        root.addLayout(row)
        self._restyle()

    def _build_summary(self, root):
        self._status_color = "accent"
        status = QFrame()
        status.setObjectName("FileCheckStatus")
        layout = QVBoxLayout(status)
        layout.setContentsMargins(18, 15, 16, 15)
        layout.setSpacing(5)

        scanning_title = "正在检查文件编号" if self._is_sequence else "正在查找重复文件"
        self._status_title = QLabel(scanning_title)
        self._status_title.setObjectName("FileCheckTitle")
        layout.addWidget(self._status_title)
        self.lbl_detail = QLabel("正在扫描所选目录及其子目录。")
        self.lbl_detail.setObjectName("FileCheckMuted")
        self.lbl_detail.setTextFormat(Qt.PlainText)
        self.lbl_detail.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.lbl_detail)

        self._progress = QProgressBar()
        self._progress.setObjectName("FileCheckProgress")
        self._progress.setAccessibleName(scanning_title)
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(4)
        layout.addSpacing(5)
        layout.addWidget(self._progress)
        root.addWidget(status)

        metrics = QHBoxLayout()
        metrics.setSpacing(8)
        results = (("folders", "缺号目录"), ("missing", "缺失编号")) if self._is_sequence else (
            ("name", "同名文件组"), ("suspect", "疑似重复组"),
        )
        self._metrics = {}
        for key, name in (("checked", "已检查文件"), *results):
            tile = QFrame()
            tile.setObjectName("FileCheckTile")
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(13, 10, 13, 10)
            tile_layout.setSpacing(2)
            label = QLabel(name)
            label.setObjectName("FileCheckMuted")
            value = QLabel("0" if key == "checked" else "—")
            value.setObjectName("FileCheckValue")
            value.setAccessibleName(name)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self._metrics[key] = value
            tile_layout.addWidget(label)
            tile_layout.addWidget(value)
            metrics.addWidget(tile, 1)
        root.addLayout(metrics)

    def _restyle(self, *_):
        panel = tk("panel")
        surface = tk("panel_2")
        border = tk("border")
        strong = tk("text_strong")
        muted = tk("text_mut")
        accent = tk(self._status_color)
        danger = "#fca5a5" if theme.is_dark else "#b91c1c"
        self.setStyleSheet(f"""
            QDialog {{ background:{panel}; }}
            QFrame#FileCheckStatus {{
                background:{surface}; border:1px solid {border};
                border-left:4px solid {accent}; border-radius:10px;
            }}
            QFrame#FileCheckTile, QFrame#FileCheckResult {{
                background:{surface}; border:1px solid {border}; border-radius:8px;
            }}
            QLabel#FileCheckTitle, QLabel#FileCheckValue {{
                background:transparent; color:{strong}; font-size:20px; font-weight:700;
            }}
            QLabel#FileCheckMuted {{
                background:transparent; color:{muted}; font-size:12px;
            }}
            QLabel#FileCheckSection, QLabel#FileCheckGroup {{
                background:transparent; color:{strong}; font-size:14px; font-weight:700;
            }}
            QLabel#FileCheckPath {{ background:transparent; color:{tk('text')}; font-size:12px; }}
            QFrame#FileCheckFileRow {{
                background:{tk('input_bg')}; border:1px solid {border}; border-radius:5px;
            }}
            QPushButton#FileCheckRecycle {{
                background:transparent; color:{danger}; border:1px solid transparent;
                border-radius:5px; padding:0; font-size:17px; font-weight:600;
            }}
            QPushButton#FileCheckRecycle:hover, QPushButton#FileCheckRecycle:focus {{
                background:{tk('hover_veil')}; border-color:{danger};
            }}
            QPushButton#FileCheckRecycle:disabled {{ color:{muted}; border-color:transparent; }}
            QLabel#SequenceNumbers {{
                background:{tk('input_bg')}; color:{tk('text')}; border:1px solid {border};
                border-radius:5px; padding:5px 8px; font-size:12px;
            }}
            QScrollArea#FileCheckScroll {{ background:transparent; border:none; }}
            QWidget#FileCheckHost, QWidget#FileCheckViewport {{ background:{panel}; }}
            QScrollBar:vertical {{ background:transparent; width:7px; margin:0; }}
            QScrollBar::handle:vertical {{ background:{tk('border_3')}; min-height:24px; border-radius:3px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background:none; }}
            QProgressBar#FileCheckProgress {{ background:{border}; border:0; border-radius:2px; }}
            QProgressBar#FileCheckProgress::chunk {{ background:{accent}; border-radius:2px; }}
        """)

    def start(self):
        if self._worker is not None:
            return
        self._running = True
        self._worker = _MultiScanWorker(
            self._roots, self._ext_filter, self, sequence_ignore_ext=self._sequence_ignore_ext,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.group.connect(self._on_group)
        self._worker.finished_scan.connect(self._on_finished)
        self._worker.start()

    def _on_progress(self, done: int, current: str):
        if self._closing:
            return
        self._metrics["checked"].setText(f"{done:,}")
        if self.btn_cancel.isEnabled():
            self.lbl_detail.setText(f"当前文件  ·  {os.path.basename(current)}")
            self.lbl_detail.setToolTip(current)

    def _on_group(self, ev):
        if self._closing or not ev:
            return
        key = ev.get("key") or ""
        files = [
            f for f in (ev.get("files") or [])
            if (f.get("path") or "") not in self._deleted
        ]
        block = self._blocks.get(key)
        if block is None:
            if len(files) < 2:
                return
            block = _DupGroupBlock(ev.get("kind") or "name", self._host, scanning=self._running)
            self._blocks[key] = block
            self._append_result(block)
            self._duplicate_counts[block.kind] += 1
            for kind, count in self._duplicate_counts.items():
                self._metrics[kind].setText(f"{count:,}")
        if not files:
            return
        if block.add_files(files):
            if self.body.isHidden():
                self._show_results()
            self.body.ensureWidgetVisible(block.header)

    def delete_listed_file(self, path: str):
        path = path or ""
        if not path or path in self._deleted:
            return
        if self._running:
            return
        try:
            if os.path.isfile(path):
                _send_to_recycle(path)
            elif os.path.exists(path):
                self._set_detail("无法删除：不是文件。")
                return
        except Exception as e:
            self._set_detail(f"删除失败：{e}")
            return
        self._deleted.add(path)
        gone = []
        for key, block in self._blocks.items():
            if block.drop_path(path) < 2:
                gone.append(key)
        for key in gone:
            block = self._blocks.pop(key, None)
            if block is None:
                continue
            self._duplicate_counts[block.kind] -= 1
            self._metrics[block.kind].setText(f"{self._duplicate_counts[block.kind]:,}")
            self._host_lay.removeWidget(block)
            block.setParent(None)
            block.deleteLater()
        if self._blocks:
            self._set_detail("已移入回收站，明细已更新。")
        else:
            self._set_status("查重明细已清空", "ok", "已移入回收站，当前没有剩余的查重结果。")
            self.body.hide()
            self.layout().activate()
            self.resize(self.width(), self.layout().sizeHint().height())

    def _add_sequence_results(self, gaps):
        for result in gaps:
            card = QFrame()
            card.setObjectName("FileCheckResult")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 10, 12, 10)
            card_layout.setSpacing(6)

            folder = result["folder"]
            folder_text = QLabel(os.path.basename(os.path.normpath(folder)) or folder)
            folder_text.setObjectName("FileCheckGroup")
            folder_text.setTextFormat(Qt.PlainText)
            folder_text.setWordWrap(True)
            folder_text.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            folder_text.setToolTip(folder_text.text())
            path = QLabel(folder)
            path.setObjectName("FileCheckMuted")
            path.setTextFormat(Qt.PlainText)
            path.setWordWrap(True)
            path.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            path.setToolTip(folder)
            path.setTextInteractionFlags(Qt.TextSelectableByMouse)
            location = QVBoxLayout()
            location.setSpacing(2)
            location.addWidget(folder_text)
            location.addWidget(path)

            header = QHBoxLayout()
            header.setSpacing(10)
            header.addLayout(location, 1)
            count = QLabel(f"缺 {result['total_missing']:,} 项")
            count.setObjectName("FileCheckGroup")
            header.addWidget(count, 0, Qt.AlignTop)
            fill_ranges = result["fill_ranges"]
            fill_size = sum(last - first + 1 for first, last, _ in fill_ranges)
            if 0 < fill_size <= 10000:
                fill = apply_medium_button(QPushButton("补全"))
                fill.setToolTip(f"从原图集补下载 {fill_size:,} 个纯数字编号的图片")
                fill.setCursor(Qt.PointingHandCursor)
                fill.clicked.connect(lambda _=False, item=result: self._request_sequence_fill(item))
                header.addWidget(fill, 0, Qt.AlignTop)
            card_layout.addLayout(header)

            labels = result["labels"]
            shown = "、".join(labels[:8]) + (" 等" if len(labels) > 8 else "")
            numbers = QLabel(shown)
            numbers.setObjectName("SequenceNumbers")
            numbers.setAccessibleName("缺失编号")
            numbers.setTextFormat(Qt.PlainText)
            numbers.setWordWrap(True)
            numbers.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            numbers.setToolTip(shown)
            numbers.setTextInteractionFlags(Qt.TextSelectableByMouse)
            card_layout.addWidget(numbers)
            self._append_result(card)

    def _request_sequence_fill(self, result):
        names = dict.fromkeys(
            f"{number:0{width}d}"
            for first, last, width in result["fill_ranges"]
            for number in range(first, last + 1)
        )
        self.fill_request = {"folder": result["folder"], "missing_names": list(names)}
        self.accept()

    def _on_finished(self, info):
        self._running = False
        if self._closing:
            return
        info = info or {}
        self.btn_cancel.setVisible(False)
        done = int(info.get("files") or 0)
        if not info.get("error"):
            self._metrics["checked"].setText(f"{done:,}")
        self._progress.hide()
        name_n = int(info.get("name_groups") or 0)
        sus_n = int(info.get("suspect_groups") or 0)
        gaps = info.get("sequence_gaps") or []
        missing = sum(result["total_missing"] for result in gaps)

        findings = []
        if gaps:
            findings.append(f"缺号 {missing} 项")
        if name_n:
            findings.append(f"同名 {name_n} 组")
        if sus_n:
            findings.append(f"疑似重复 {sus_n} 组")
        findings_text = "，".join(findings)
        if info.get("empty"):
            summary = "没有可检查的目录"
        elif info.get("error"):
            summary = "检查失败"
        elif info.get("cancelled"):
            summary = "检查已取消"
        elif not self._is_sequence and self._ext_filter is False:
            summary = "查重文件类型均已关闭"
        elif done == 0:
            summary = "没有可检查的文件"
        elif self._is_sequence:
            summary = findings_text or "未发现缺号"
        else:
            summary = findings_text or "未发现同名或疑似重复"
        err_n = int(info.get("errors") or 0)
        if err_n:
            summary += f"，已跳过 {err_n} 处无法访问的项目"
        self.scan_summary = summary
        if self._is_sequence:
            self._present_sequence_result(info, done, err_n, gaps, missing)
        else:
            self._present_duplicate_result(info, done, err_n, name_n, sus_n)

    def _present_sequence_result(self, info, done, errors, gaps, missing):
        incomplete = info.get("error") or info.get("cancelled") or info.get("empty")
        if not incomplete and done:
            self._metrics["folders"].setText(f"{len(gaps):,}")
            self._metrics["missing"].setText(f"{missing:,}")

        if info.get("error"):
            title, color = "检查失败", "err"
            detail = "可关闭窗口，确认目录可访问后重新检查。"
        elif info.get("cancelled"):
            title, color = "检查已取消", "warn"
            detail = "扫描提前结束，尚未生成缺号结果。"
        elif info.get("empty"):
            title, color = "没有可检查的目录", "warn"
            detail = "请确认所选目录仍然存在，再重新检查。"
        elif not done:
            title, color = "没有可检查的文件", "warn"
            detail = "所选目录中没有找到可读取的文件。"
        elif gaps:
            title, color = "发现缺失编号", "warn"
            detail = "下方列出了缺号目录和缺失编号。"
        elif errors:
            title, color = "已检查部分未发现缺号", "warn"
            detail = "可访问的文件已完成检查。"
        else:
            title, color = "未发现缺号", "ok"
            detail = "所选目录已检查完毕。"

        if errors and not info.get("error"):
            detail = f"{detail} 已跳过 {errors:,} 处无法访问的项目。"
        self._set_status(title, color, detail, info.get("error"))

        compact_height = self.layout().sizeHint().height()
        if gaps:
            self._add_sequence_results(gaps)
            self._compact_height = compact_height
            self._show_results()
            self.body.verticalScrollBar().setValue(0)
        elif self.height() <= self._compact_height:
            self.resize(self.width(), compact_height)

    def _present_duplicate_result(self, info, done, errors, name_n, sus_n):
        for block in self._blocks.values():
            block.finish_scan()
        has_results = bool(self._blocks)
        if info.get("error"):
            title, color = "查重失败", "err"
            detail = "下方仅显示失败前找到的结果。" if has_results else "请确认目录可访问，再重新检查。"
        elif info.get("cancelled"):
            title, color = "查重已取消", "warn"
            detail = "下方仅显示取消前找到的结果。" if has_results else "扫描提前结束，未完成查重。"
        elif info.get("empty"):
            title, color = "没有可检查的目录", "warn"
            detail = "请确认所选目录仍然存在，再重新检查。"
        elif self._ext_filter is False:
            title, color = "查重文件类型均已关闭", "warn"
            detail = "开启至少一种文件类型后，可重新查重。"
        elif not done:
            title, color = "没有符合条件的文件", "warn"
            detail = "请检查所选目录和文件类型筛选。"
        else:
            self._metrics["name"].setText(f"{name_n:,}")
            self._metrics["suspect"].setText(f"{sus_n:,}")
            if has_results:
                title, color = "查重完成，发现匹配项", "warn"
                detail = "疑似重复组按文件大小匹配，请核对后再处理。" if sus_n else "下方可查看同名文件的位置、大小和修改时间。"
            elif errors:
                title, color = "已检查的文件中未发现匹配项", "warn"
                detail = "可访问的文件已完成检查。"
            else:
                title, color = "未发现同名或疑似重复", "ok"
                detail = "所选目录中符合筛选条件的文件均已检查。"

        if errors and not info.get("error"):
            detail = f"{detail} 已跳过 {errors:,} 处无法访问的项目。"
        self._set_status(title, color, detail, info.get("error"))
        if has_results:
            self._show_results()
        elif self.height() <= self._compact_height:
            self.resize(self.width(), self.layout().sizeHint().height())

    def _set_detail(self, text, tooltip=None):
        self.lbl_detail.setText(text)
        self.lbl_detail.setToolTip(str(tooltip or text))

    def _set_status(self, title, color, detail, tooltip=None):
        self._status_title.setText(title)
        self._set_detail(detail, tooltip)
        if color != self._status_color:
            self._status_color = color
            self._restyle()

    def _append_result(self, widget):
        self._host_lay.insertWidget(self._host_lay.count() - 1, widget)

    def _show_results(self):
        self.body.setMinimumHeight(96)
        self.body.show()
        self._results_resize.start(0)

    def _fit_results(self):
        if self._closing or self.body.isHidden():
            return
        self._host_lay.activate()
        margins = self.layout().contentsMargins()
        content_width = self.width() - margins.left() - margins.right()
        scroll_width = self.body.verticalScrollBar().sizeHint().width()
        content_height = self._host_lay.totalHeightForWidth(content_width - scroll_width)
        results_limit = 290 if self._is_sequence else 360
        screen = self.screen()
        if screen is not None:
            available = screen.availableGeometry()
            results_limit = min(results_limit, available.height() - self._compact_height - 64)
        results_height = max(132, min(content_height + 12, results_limit))
        target_height = self._compact_height + self.layout().spacing() + results_height
        if target_height > self.height():
            self.resize(self.width(), target_height)
            if screen is not None and self.isVisible():
                overflow = self.frameGeometry().bottom() - available.bottom()
                if overflow > 0:
                    self.move(self.x(), max(available.top(), self.y() - overflow))

    def _on_cancel(self):
        w = self._worker
        if w is not None:
            w.stop()
        self._status_title.setText("正在取消检查" if self._is_sequence else "正在取消查重")
        self._set_detail("等待扫描停止…")
        self.btn_cancel.setEnabled(False)

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

    def shutdown(self):
        self._closing = True
        stop_qthread(self._worker, name="dup_multi_scan")


def _dir_paths_from_mime(md) -> list:
    if md is None or not md.hasUrls():
        return []
    out = []
    for url in md.urls():
        try:
            if not url.isLocalFile():
                continue
            p = url.toLocalFile()
        except Exception:
            continue
        if p and os.path.isdir(p):
            out.append(os.path.normpath(p))
    return out


def _mime_may_have_local(md) -> bool:
    if md is None:
        return False
    try:
        if md.hasUrls():
            return True
    except Exception:
        pass
    try:
        if md.hasFormat("text/uri-list"):
            return True
    except Exception:
        pass
    return False


class MultiDirDrop(QFrame):
    """多目录查重的拖放区：只收文件夹。"""

    paths_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DupMultiDrop")
        self.setAccessibleName("添加查重文件夹")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAcceptDrops(True)
        self.setMinimumHeight(72)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._paths = []
        self._keys = set()
        self._hover = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(0)

        self.empty = QLabel()
        self.empty.setPixmap(self.style().standardIcon(QStyle.SP_DirIcon).pixmap(32, 32))
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.empty.setStyleSheet("background:transparent;")
        lay.addWidget(self.empty, 1)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("DupMultiDropScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.host = QWidget()
        self.host.setObjectName("DupMultiDropHost")
        self.flow = QVBoxLayout(self.host)
        self.flow.setContentsMargins(0, 0, 0, 0)
        self.flow.setSpacing(4)
        self.flow.addStretch(1)
        self.scroll.setWidget(self.host)
        self.scroll.setVisible(False)
        lay.addWidget(self.scroll, 1)

        for w in (self.empty, self.scroll, self.scroll.viewport(), self.host):
            self._watch(w)
        self.refresh_theme()
        try:
            theme.changed.connect(self.refresh_theme)
        except Exception:
            pass

    def mousePressEvent(self, e):
        # 空列表时点击打开选目录；有文件夹时不拦截，避免抢掉移除按钮等交互
        if (
            e is not None
            and e.button() == Qt.LeftButton
            and not self._paths
        ):
            self.pick_directory()
            e.accept()
            return
        super().mousePressEvent(e)

    def pick_directory(self):
        path = QFileDialog.getExistingDirectory(self, "选择要统计的目录", "")
        if not path:
            return
        self.add_paths([os.path.normpath(path)])

    def paths(self) -> list:
        return list(self._paths)

    def clear_paths(self):
        """一次清掉已拖入的文件夹，不删除磁盘上的目录。"""
        if not self._paths and not self._keys:
            return
        self._paths = []
        self._keys = set()
        self._rebuild()
        self.paths_changed.emit()

    def _watch(self, w):
        if w is None:
            return
        try:
            w.setAcceptDrops(True)
            w.installEventFilter(self)
        except Exception:
            pass

    def eventFilter(self, obj, ev):
        t = ev.type() if ev is not None else None
        if t == QEvent.DragEnter:
            self.dragEnterEvent(ev)
            return bool(ev.isAccepted())
        if t == QEvent.DragMove:
            self.dragMoveEvent(ev)
            return bool(ev.isAccepted())
        if t == QEvent.DragLeave:
            self.dragLeaveEvent(ev)
            return False
        if t == QEvent.Drop:
            self.dropEvent(ev)
            return bool(ev.isAccepted())
        return super().eventFilter(obj, ev)

    def dragEnterEvent(self, e):
        if _mime_may_have_local(e.mimeData()):
            e.acceptProposedAction()
            self._set_hover(True)
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if _mime_may_have_local(e.mimeData()):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self._set_hover(False)
        try:
            e.accept()
        except Exception:
            pass

    def dropEvent(self, e):
        self._set_hover(False)
        paths = _dir_paths_from_mime(e.mimeData())
        if not paths:
            e.ignore()
            return
        e.acceptProposedAction()
        self.add_paths(paths)

    def add_paths(self, paths):
        changed = False
        for raw in paths or []:
            if not raw or not os.path.isdir(raw):
                continue
            abs_p = os.path.abspath(raw)
            key = os.path.normcase(abs_p)
            if key in self._keys:
                continue
            self._keys.add(key)
            self._paths.append(abs_p)
            changed = True
        if changed:
            self._rebuild()
            self.paths_changed.emit()

    def _remove(self, key: str):
        self._paths = [
            p for p in self._paths
            if os.path.normcase(os.path.abspath(p)) != key
        ]
        self._keys.discard(key)
        self._rebuild()
        self.paths_changed.emit()

    def _leaf(self, path: str) -> str:
        base = os.path.basename(path.rstrip("\\/")) or path
        leaves = [
            os.path.basename(p.rstrip("\\/")) or p
            for p in self._paths
        ]
        if leaves.count(base) > 1:
            parent = os.path.basename(os.path.dirname(path.rstrip("\\/"))) or ""
            if parent:
                return f"{base}  ({parent})"
        return base

    def _rebuild(self):
        lay = self.flow
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        has = bool(self._paths)
        self.empty.setVisible(not has)
        self.scroll.setVisible(has)
        try:
            self.setCursor(Qt.PointingHandCursor if not has else Qt.ArrowCursor)
        except Exception:
            pass
        for path in self._paths:
            lay.addWidget(self._row(path))
        lay.addStretch(1)

    def _row(self, path: str) -> QWidget:
        key = os.path.normcase(os.path.abspath(path))
        row = QWidget()
        row.setObjectName("DupMultiDropRow")
        row.setAttribute(Qt.WA_StyledBackground, True)
        row.setStyleSheet(
            f"#DupMultiDropRow{{background:{tk('panel_2')};border-radius:6px;}}"
        )
        hl = QHBoxLayout(row)
        hl.setContentsMargins(8, 2, 4, 2)
        hl.setSpacing(4)
        lab = QLabel(self._leaf(path))
        lab.setToolTip(path)
        lab.setStyleSheet(
            f"background:transparent;color:{tk('text')};font-size:12px;"
        )
        hl.addWidget(lab, 1)
        btn = QPushButton("✕")
        btn.setObjectName("DupMultiDropDel")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setFixedSize(18, 18)
        btn.setToolTip("从列表移除")
        btn.setStyleSheet(
            "QPushButton#DupMultiDropDel{background:transparent;border:none;"
            f"color:{tk('text_mut')};font-size:12px;font-weight:700;padding:0;}}"
            "QPushButton#DupMultiDropDel:hover{color:#ef4444;}"
        )
        btn.clicked.connect(lambda _=False, k=key: self._remove(k))
        hl.addWidget(btn, 0)
        self._watch(row)
        self._watch(lab)
        return row

    def _set_hover(self, hover: bool):
        hover = bool(hover)
        if hover == self._hover:
            return
        self._hover = hover
        self._apply_frame()

    def _apply_frame(self):
        bd = "#facc15" if self._hover else "#c59800"
        self.setStyleSheet(
            f"#DupMultiDrop{{background:{tk('input_bg')};"
            f"border:1px dashed {bd};border-radius:8px;}}"
        )
        self.scroll.setStyleSheet(
            "#DupMultiDropScroll{background:transparent;border:none;}"
            "#DupMultiDropHost{background:transparent;}"
        )

    def refresh_theme(self, *_):
        self._apply_frame()
        self._rebuild()


class PageDupClean(QWidget):
    def hasHeightForWidth(self):
        return False

    def minimumSizeHint(self):
        return QSize(0, 0)

    def sizeHint(self):
        sh = super().sizeHint()
        if not sh.isValid() or sh.height() <= 0:
            return QSize(0, 0)
        cap = 700
        if int(sh.height()) > cap:
            return QSize(sh.width(), cap)
        return sh

    def __init__(self):
        super().__init__()
        apply_transparent_surface(self, "PageDupClean")
        self._worker = None
        self._dialog = None
        self._last_dir = ""
        self._theme_titles = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addStretch(1)

        mid = QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.addStretch(1)

        card = make_card("CardDupClean")
        card.setMaximumWidth(520)
        card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        vb = QVBoxLayout(card)
        vb.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        vb.setSpacing(CARD_TITLE_BODY_GAP)
        self._theme_titles.append(install_card_title(card, vb, "同名清理"))

        self.btn = QPushButton("选择目录并统计")
        apply_btn_download(self.btn)
        self.btn.setMinimumHeight(36)
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.clicked.connect(self._on_click)
        vb.addWidget(self.btn)

        self.hint = QLabel(
            "点按钮选择目录，统计其中全部文件与文件夹。\n"
            "报告里可「去除同名」：同名且大小相同的，只保留日期最新的一份。"
        )
        self.hint.setProperty("typo", "muted")
        self.hint.setWordWrap(True)
        vb.addWidget(self.hint)

        mid.addWidget(card, 0)
        mid.addStretch(1)
        outer.addLayout(mid)
        outer.addStretch(2)

        try:
            theme.changed.connect(self.refresh_theme)
        except Exception:
            pass

    def refresh_theme(self, *_):
        for lbl in self._theme_titles:
            restyle_card_title(lbl)
        card = self.findChild(QFrame, "CardDupClean")
        if card is not None:
            restyle_card_frame(card)
        apply_btn_download(self.btn)
        scanning = self._worker is not None and self._worker.isRunning()
        if not scanning and "取消" not in (self.btn.text() or ""):
            self.btn.setText("选择目录并统计")

    def _on_click(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            self.btn.setEnabled(False)
            self.btn.setText("正在取消…")
            self.hint.setText("正在停止扫描…")
            return
        start = self._last_dir if os.path.isdir(self._last_dir) else ""
        d = QFileDialog.getExistingDirectory(self, "选择要统计的目录", start)
        if not d:
            return
        root = os.path.normpath(d)
        if not os.path.isdir(root):
            QMessageBox.warning(self, "同名清理", "所选目录不存在。")
            return
        self._last_dir = root
        self._start_scan(root)

    def _start_scan(self, root: str):
        stop_qthread(self._worker, name="dup_clean_scan")
        self._worker = _ScanWorker(root, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_done)
        self.btn.setText("取消扫描")
        self.hint.setText(f"正在扫描：{root}")
        self._worker.start()

    def _on_progress(self, files: int, folders: int, current: str):
        self.hint.setText(
            f"已扫描 {files} 个文件，{folders} 个文件夹\n{current}"
        )

    def _on_done(self, result: ScanResult):
        self.btn.setEnabled(True)
        self.btn.setText("选择目录并统计")
        apply_btn_download(self.btn)
        if result is None:
            self.hint.setText("扫描失败。")
            return
        if result.cancelled:
            self.hint.setText("已取消扫描。")
            return
        self.hint.setText(
            f"{result.root}\n"
            f"文件夹 {result.folder_count} · 文件 {result.file_count} · "
            f"总大小 {_fmt_size(result.total_size)}"
        )
        dlg = _ReportDialog(self.window() or self, result)
        self._dialog = dlg
        dlg.exec_()
        self._dialog = None

    def shutdown(self):
        if self._worker is not None:
            try:
                self._worker.stop()
            except Exception:
                pass
        stop_qthread(self._worker, name="dup_clean_scan")
        dlg = self._dialog
        if dlg is not None:
            try:
                dlg.shutdown()
                dlg.close()
            except Exception:
                pass
            self._dialog = None
