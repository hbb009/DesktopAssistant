# pages/disk_treemap_widget.py
# 磁盘空间分析组件：盘符切换（紧凑横向 Tab） + Treemap（QPainter 原生绘制，不依赖 WebEngine）。
# Treemap 布局复刻原网页版 Canvas squarify 算法，保证视觉效果与最初测试版本一致；
# 数据扫描仍由 Python 完成（scan_drives / scan_top_level）。

import os
import string
import ctypes

from styles.style_all import (
    theme,
    fmt,
    tk,
    apply_transparent_surface,
    DRIVE_TAB_ACTIVE_QSS,
    DRIVE_TAB_ACTIVE_MAIN_QSS,
    DRIVE_TAB_ACTIVE_UNDERLINE_QSS,
    DRIVE_TAB_INACTIVE_QSS,
    DRIVE_TAB_INACTIVE_MAIN_QSS,
    DRIVE_TAB_INACTIVE_UNDERLINE_QSS,
    DRIVE_ICON_QSS,
    DRIVE_MAIN_BASE_QSS,
    TAB_BAR_QSS,
    SCAN_BAR_QSS,
    SCAN_BTN_QSS,
    SCAN_PROGRESS_QSS,
    SCAN_STATUS_QSS,
    FILES_PANEL_QSS,
    FILES_TITLE_QSS,
    FILES_LIST_QSS,
    FILE_RANK_ROW_QSS,
    FILE_RANK_NUM_QSS,
    FILE_RANK_NAME_QSS,
    FILE_RANK_SIZE_QSS,
)

# Treemap 画布底色：这是数据可视化表面，色块调色板是为深底设计的，
# 因此两套主题下都保持深色（与 WizTree 等工具一致）。
# 想让它跟随主题，把下面这行改成 None，_treemap_bg() 就会用 tk("canvas")。
TREEMAP_CANVAS_BG = "#111110"


def _treemap_bg() -> str:
    return TREEMAP_CANVAS_BG or tk("canvas")
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QRectF
from PyQt5.QtGui import QFont, QFontMetrics, QPainter, QColor, QPen
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy, QFrame,
    QListWidget, QListWidgetItem, QProgressBar, QPushButton
)


# ----------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------
def fmt_size(gb: float) -> str:
    if gb < 0.001:
        return "0 KB"
    if gb < 1:
        return f"{gb * 1024:.0f} MB"
    return f"{gb:.1f} GB"


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


PALETTE = [
    "#b84a14", "#183878", "#3a5068", "#6a6810", "#585858",
    "#702808", "#154a88", "#305010", "#801840", "#a03010",
]


def scan_drives():
    """扫描系统实际存在的盘符及容量（Windows）。失败返回空列表。"""
    drives = []
    if os.name != "nt":
        return drives
    try:
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for i, letter in enumerate(string.ascii_uppercase):
            if not (bitmask >> i) & 1:
                continue
            root = f"{letter}:\\"
            try:
                free_bytes = ctypes.c_ulonglong(0)
                total_bytes = ctypes.c_ulonglong(0)
                ok = ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                    ctypes.c_wchar_p(root), None,
                    ctypes.byref(total_bytes), ctypes.byref(free_bytes)
                )
                if not ok or total_bytes.value == 0:
                    continue
                total_gb = total_bytes.value / (1024 ** 3)
                free_gb = free_bytes.value / (1024 ** 3)
                used_gb = total_gb - free_gb
                drives.append({
                    "letter": f"{letter}:",
                    "label": "本地磁盘",
                    "total": total_gb,
                    "used": used_gb,
                })
            except Exception:
                continue
    except Exception:
        pass
    return drives



def _dir_size_via_system(path: str, timeout: float = 20.0):
    """
    [已废弃，保留函数签名以兼容旧调用] 曾尝试用 PowerShell 子进程加速扫描，
    但发现一个严重问题：PowerShell 的 Sort-Object/Get-ChildItem 在处理
    大盘符（几十万文件）时是阻塞性全量缓冲操作，且 subprocess.run 的
    timeout 在 Windows 上经常无法真正杀死挂起的 PowerShell 子进程
    （尤其是卡在系统保护目录的权限调用时），导致整个程序看起来"死机"。
    现在统一改用纯 Python 遍历方案（见 _scan_drive_combined），不再依赖
    外部子进程，所有超时控制都在 Python 主控逻辑里完成，可靠可中断。
    """
    return None


# 扫描时跳过的系统保留目录：这些目录权限复杂、文件数量巨大且用户基本不需要
# 看到内部明细，跳过可以大幅提速且避免触发权限异常导致的卡顿
_SKIP_DIR_NAMES = {
    "System Volume Information", "$Recycle.Bin", "$RECYCLE.BIN",
    "Recovery", "$WinREAgent",
}


def _dir_size_full(path: str, _deadline: list, largest_files: list, top_n: int = 15) -> int:
    """
    单次遍历同时完成两件事：① 累加目录总大小 ② 收集体积最大的文件
    （用简单的"维护一个小顶堆/有序列表"方式，避免对全盘文件做完整排序）。
    用共享的 _deadline（截止时间戳）做硬性超时保护：一旦超时立刻停止
    深入子目录，只返回已经累计的大小（偏小不会偏大，不会导致占比夸大）。
    largest_files 是调用方传入的共享列表，元素为 (size_bytes, path)，
    内部保持按大小降序、长度不超过 top_n，全程 O(top_n) 的插入开销，
    不会因为文件数量巨大而显著拖慢扫描。
    """
    import time
    total = 0
    try:
        for entry in os.scandir(path):
            if time.time() > _deadline[0]:
                break
            try:
                if entry.is_symlink():
                    continue
                if entry.name in _SKIP_DIR_NAMES:
                    continue
                if entry.is_file():
                    size = entry.stat().st_size
                    total += size
                    if largest_files is not None and size > 0:
                        if len(largest_files) < top_n or size > largest_files[-1][0]:
                            largest_files.append((size, entry.path))
                            largest_files.sort(key=lambda x: x[0], reverse=True)
                            if len(largest_files) > top_n:
                                largest_files.pop()
                elif entry.is_dir():
                    total += _dir_size_full(entry.path, _deadline, largest_files, top_n)
            except Exception:
                continue
    except Exception:
        pass
    return total





def scan_drive_combined(root_path: str, used_total_gb: float = None,
                         max_items: int = 20, max_files: int = 15,
                         total_time_budget: float = 30.0, progress_cb=None):
    """
    单次遍历同时完成"顶层文件夹大小统计"和"最大文件排行榜"两件事，
    用一个【全局硬性总时间上限】（默认 30 秒）控制，不再按文件夹数量
    或单文件夹分配时间，避免任何一种分配策略失衡导致的卡死或漏算。

    progress_cb（可选）：每完成一个顶层条目的扫描就调用一次
    progress_cb(done_count, total_count, current_name)，供 UI 端
    渲染真实进度条，而不是一个无法预知进度的转圈动画。

    关键修复历史（从最初版本到现在踩过的坑）：
    1) depth_limit=1 浅层估算 → 严重低估大文件夹真实大小，占比失真。
    2) 全部条目共享一个 deadline → 前面的大文件夹耗尽预算后，后面的
       文件夹完全扫不到，被"其他"吞掉。
    3) 改成"总预算 / 条目数"平均分配 → 条目越多、超大文件夹分到的时间
       越少，越大的文件夹反而越容易超时失败、整个消失（如 428GB 的
       ComfyUI 完全没出现在结果里）。
    4) 改用 PowerShell 子进程做 Get-ChildItem + Sort-Object 全盘排序
       → 看似能加速，但对几十万文件的大盘符是阻塞性全量缓冲操作，
       加上 subprocess.run 的 timeout 在 Windows 上经常杀不死挂起的
       PowerShell 子进程（尤其卡在系统保护目录权限调用时），导致
       程序看起来彻底"死机"，用户等了 10 分钟都没反应。
    （本次修复，当前版本）彻底放弃外部子进程，回归纯 Python os.scandir
    遍历，但用【一个共享的全局 deadline】贯穿整个扫描过程（而不是
    每层/每个条目各算各的），这样无论目录结构多复杂，扫描总耗时都有
    严格的硬上限，到点就停，绝不会出现挂起或长时间无响应的情况。
    代价是：如果在时间预算内没扫完，体积会偏小（计入"其他"），
    但程序永远不会卡死，这是更重要的可靠性前提。
    """
    import time
    items = []
    largest_files = []  # [(size_bytes, path), ...] 降序，长度 <= max_files

    try:
        entries = [e for e in os.scandir(root_path) if not e.is_symlink()
                   and e.name not in _SKIP_DIR_NAMES]
    except Exception:
        return [], []

    if not entries:
        return [], []

    total_count = len(entries)
    deadline = [time.time() + total_time_budget]

    for idx, entry in enumerate(entries):
        if progress_cb is not None:
            try:
                progress_cb(idx, total_count, entry.name)
            except Exception:
                pass
        if time.time() > deadline[0]:
            # 总预算已耗尽：剩余未扫描到的顶层条目直接跳过，
            # 它们的大小会自然计入后面的"其他"补齐块，不会导致程序继续等待
            break
        try:
            if entry.is_dir():
                size = _dir_size_full(entry.path, deadline, largest_files, max_files)
            else:
                size = entry.stat().st_size
                if size > 0:
                    if len(largest_files) < max_files or size > largest_files[-1][0]:
                        largest_files.append((size, entry.path))
                        largest_files.sort(key=lambda x: x[0], reverse=True)
                        if len(largest_files) > max_files:
                            largest_files.pop()
            gb = size / (1024 ** 3)
            if gb <= 0.001:
                continue
            items.append({
                "n": entry.name,
                "s": round(gb, 4),
                "c": PALETTE[idx % len(PALETTE)],
                "ch": [],
            })
        except Exception:
            continue

    if progress_cb is not None:
        try:
            progress_cb(total_count, total_count, "")
        except Exception:
            pass

    items.sort(key=lambda x: x["s"], reverse=True)
    items = items[:max_items]

    # 用真实已用空间补齐"其他"灰块，确保 Treemap 面积比例准确，
    # 不会因为总时间预算耗尽提前停止扫描而导致占比失真
    if used_total_gb is not None:
        shown_sum = sum(i["s"] for i in items)
        remainder = used_total_gb - shown_sum
        if remainder > 0.05:
            items.append({
                "n": "其他 / 未单独列出",
                "s": round(remainder, 4),
                "c": "#3a3a36",
                "ch": [],
            })

    files_result = [
        {
            "path": p,
            "name": os.path.basename(p),
            "size_gb": round(s / (1024 ** 3), 3),
        }
        for s, p in largest_files
    ]

    return items, files_result


def scan_top_level(root_path: str, max_items: int = 20, used_total_gb: float = None, **kwargs):
    """向后兼容包装：仅返回目录树部分（内部走合并扫描，避免重复遍历）"""
    items, _ = scan_drive_combined(root_path, used_total_gb=used_total_gb, max_items=max_items)
    return items


def scan_largest_files(root_path: str, top_n: int = 15, **kwargs):
    """向后兼容包装：仅返回大文件排行榜部分（内部走合并扫描，避免重复遍历）"""
    _, files = scan_drive_combined(root_path, max_files=top_n)
    return files


DEMO_LARGEST_FILES = [
    {"path": "D:\\ComfyUI\\models\\diffusion_models\\flux2.safetensors", "name": "flux2.safetensors", "size_gb": 23.4},
    {"path": "D:\\ComfyUI\\models\\Qwen\\Qwen2.5-VL-7B\\model.safetensors", "name": "model.safetensors", "size_gb": 15.8},
    {"path": "D:\\sd-webui-aki-v4.10\\models\\Stable-diffusion\\sdxl_base.safetensors", "name": "sdxl_base.safetensors", "size_gb": 6.9},
    {"path": "C:\\pagefile.sys", "name": "pagefile.sys", "size_gb": 5.7},
    {"path": "D:\\ComfyUI\\models\\loras\\detail_tweaker.safetensors", "name": "detail_tweaker.safetensors", "size_gb": 2.1},
    {"path": "D:\\WorkYS\\2026制作\\project_final.mp4", "name": "project_final.mp4", "size_gb": 1.8},
]


# ----------------------------------------------------------------------
# 占位演示数据（扫描失败 / 非 Windows 环境下兜底展示）
# ----------------------------------------------------------------------
DEMO_DRIVES = [
    {"letter": "C:", "label": "系统盘", "total": 230.3, "used": 197.4, "tree": [
        {"n": "Users", "s": 75.7, "c": "#b84a14", "ch": [
            {"n": "Hu", "s": 42.1, "c": "#c85820", "ch": [
                {"n": "AppData", "s": 18.3, "c": "#a03810", "ch": [
                    {"n": "Local", "s": 11.2, "c": "#8a2e0a", "ch": []},
                    {"n": "Roaming", "s": 5.8, "c": "#b84818", "ch": [
                        {"n": "SodaMusic", "s": 1.9, "c": "#d05820", "ch": []},
                        {"n": "LunaCacheV2", "s": 1.4, "c": "#a03010", "ch": []},
                    ]},
                ]},
                {"n": "JianyingPro", "s": 8.4, "c": "#d06828", "ch": []},
                {"n": "Projects", "s": 6.2, "c": "#c05818", "ch": []},
                {"n": "User Data", "s": 5.4, "c": "#b04818", "ch": []},
                {"n": "com.lveditor.draft", "s": 3.8, "c": "#e07830", "ch": []},
            ]},
            {"n": "Programs", "s": 18.6, "c": "#1a7040", "ch": [
                {"n": "Google", "s": 8.2, "c": "#145830", "ch": [
                    {"n": "Chrome", "s": 7.8, "c": "#104828", "ch": []},
                ]},
                {"n": "Slack", "s": 4.2, "c": "#186038", "ch": []},
            ]},
            {"n": "desktop.ini", "s": 0.3, "c": "#504840", "ch": []},
        ]},
        {"n": "Program Files", "s": 53.1, "c": "#183878", "ch": [
            {"n": "Microsoft Visual Studio", "s": 22.4, "c": "#1a4488", "ch": [
                {"n": "2022", "s": 20.1, "c": "#184080", "ch": [
                    {"n": "Community", "s": 8.4, "c": "#122e78", "ch": []},
                    {"n": "VC", "s": 5.6, "c": "#183470", "ch": []},
                    {"n": "Common7", "s": 3.8, "c": "#1c2c80", "ch": []},
                    {"n": "MSVC", "s": 1.1, "c": "#101060", "ch": []},
                ]},
            ]},
            {"n": "Adobe", "s": 8.2, "c": "#5818a0", "ch": []},
            {"n": "Autodesk", "s": 7.6, "c": "#183a78", "ch": []},
            {"n": "WindowsApps", "s": 4.8, "c": "#144e88", "ch": []},
            {"n": "Intel", "s": 1.4, "c": "#2878c8", "ch": []},
        ]},
        {"n": "Windows", "s": 42.8, "c": "#3a5068", "ch": [
            {"n": "System32", "s": 18.4, "c": "#304860", "ch": [
                {"n": "drivers", "s": 3.2, "c": "#283a50", "ch": []},
                {"n": "LogFiles", "s": 0.8, "c": "#202e40", "ch": []},
                {"n": "Temp", "s": 0.7, "c": "#283848", "ch": []},
            ]},
            {"n": "WinSxS", "s": 14.6, "c": "#283858", "ch": []},
            {"n": "Temp", "s": 1.2, "c": "#384e60", "ch": []},
        ]},
        {"n": "Program Files (x86)", "s": 13.1, "c": "#6a6810", "ch": [
            {"n": "Microsoft", "s": 5.4, "c": "#585810", "ch": []},
            {"n": "NVIDIA", "s": 1.4, "c": "#4a8018", "ch": []},
        ]},
        {"n": "ProgramData", "s": 10.6, "c": "#585858", "ch": [
            {"n": "Microsoft", "s": 4.8, "c": "#484848", "ch": []},
            {"n": "chocolatey", "s": 0.9, "c": "#505050", "ch": []},
        ]},
        {"n": "pagefile.sys", "s": 5.7, "c": "#c01010", "ch": []},
        {"n": "$Recycle.Bin", "s": 0.37, "c": "#303030", "ch": []},
    ]},
    {"letter": "D:", "label": "数据盘", "total": 500, "used": 312, "tree": [
        {"n": "影视资源", "s": 120.4, "c": "#154a88", "ch": [
            {"n": "电影", "s": 68.2, "c": "#103870", "ch": []},
            {"n": "美剧", "s": 32.1, "c": "#103068", "ch": []},
            {"n": "纪录片", "s": 20.1, "c": "#0c2858", "ch": []},
        ]},
        {"n": "工作文件", "s": 85.2, "c": "#305010", "ch": [
            {"n": "设计源文件", "s": 38.1, "c": "#284010", "ch": []},
            {"n": "项目归档", "s": 28.4, "c": "#304818", "ch": []},
            {"n": "素材库", "s": 18.7, "c": "#203808", "ch": []},
        ]},
        {"n": "游戏", "s": 64.8, "c": "#702808", "ch": [
            {"n": "Steam Games", "s": 48.4, "c": "#602008", "ch": []},
            {"n": "Epic Games", "s": 16.4, "c": "#803010", "ch": []},
        ]},
        {"n": "备份", "s": 30.5, "c": "#505050", "ch": []},
        {"n": "音乐", "s": 8.4, "c": "#801840", "ch": []},
        {"n": "照片", "s": 2.7, "c": "#a03010", "ch": []},
    ]},
    {"letter": "E:", "label": "移动盘", "total": 128, "used": 43.6, "tree": [
        {"n": "项目源码", "s": 18.2, "c": "#154878", "ch": [
            {"n": "node_modules", "s": 9.4, "c": "#103060", "ch": []},
            {"n": "src", "s": 5.1, "c": "#102858", "ch": []},
        ]},
        {"n": "文档资料", "s": 12.4, "c": "#1e3e0e", "ch": []},
        {"n": "临时文件", "s": 8.9, "c": "#701408", "ch": []},
        {"n": "图片素材", "s": 4.1, "c": "#803810", "ch": []},
    ]},
]


# ----------------------------------------------------------------------
# 紧凑横向盘符 Tab（参考截图：图标 + 文字一行 + 空间占用 + 下划线）
# ----------------------------------------------------------------------
class DriveTab(QWidget):
    def __init__(self, letter, used_gb, total_gb, parent=None):
        super().__init__(parent)
        self.setObjectName("DriveTab")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(44)
        # 窄窗下多盘符并排：原 220 过宽，略收后约 3 个盘可露在 ~700 内容宽内
        self.setFixedWidth(168)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 0)
        lay.setSpacing(1)

        row = QHBoxLayout()
        row.setSpacing(8)

        self.icon = QLabel("💾")
        self.icon.setStyleSheet(fmt(DRIVE_ICON_QSS))
        self.lbl_main = QLabel(f"{letter} 本地磁盘")
        self.lbl_main.setObjectName("DriveTabMain")
        f = QFont("Microsoft YaHei", 13, QFont.DemiBold)
        self.lbl_main.setFont(f)
        self.lbl_main.setStyleSheet(fmt(DRIVE_MAIN_BASE_QSS))

        row.addWidget(self.icon, 0)
        row.addWidget(self.lbl_main, 0)
        row.addStretch(1)
        lay.addLayout(row)

        # 使用率百分比留作字段，主题切换时要用它重算警告色
        self._pct = (used_gb / total_gb * 100) if total_gb else 0
        self.lbl_sub = QLabel(f"{fmt_size(used_gb)} / {fmt_size(total_gb)} · {self._pct:.0f}%")
        self.lbl_sub.setObjectName("DriveTabSub")
        lay.addWidget(self.lbl_sub)

        self.underline = QFrame()
        self.underline.setFixedHeight(3)
        lay.addWidget(self.underline)

        self._active = False
        self._apply_style()

    def set_active(self, active: bool):
        self._active = active
        self._apply_style()

    def refresh_theme(self, *_):
        """主题切换时由父组件调用。只重刷样式，不重建控件。"""
        self.icon.setStyleSheet(fmt(DRIVE_ICON_QSS))
        self._apply_style()

    def _pct_color(self) -> str:
        """使用率警告色：>85% 红，>70% 橙，其余跟随主题的弱化文字色。"""
        if self._pct > 85:
            return tk("err")
        if self._pct > 70:
            return tk("warn")
        return tk("text_dim")

    def _apply_style(self):
        self.lbl_sub.setStyleSheet(
            f"color:{self._pct_color()}; font-size:12px; background:transparent;")
        if self._active:
            self.setStyleSheet(fmt(DRIVE_TAB_ACTIVE_QSS))
            self.lbl_main.setStyleSheet(fmt(DRIVE_TAB_ACTIVE_MAIN_QSS))
            self.underline.setStyleSheet(fmt(DRIVE_TAB_ACTIVE_UNDERLINE_QSS))
        else:
            self.setStyleSheet(fmt(DRIVE_TAB_INACTIVE_QSS))
            self.lbl_main.setStyleSheet(fmt(DRIVE_TAB_INACTIVE_MAIN_QSS))
            self.underline.setStyleSheet(fmt(DRIVE_TAB_INACTIVE_UNDERLINE_QSS))


# ----------------------------------------------------------------------
# Treemap 原生绘制（QPainter 实现，替代原 WebEngine Canvas 版本）
# ----------------------------------------------------------------------

def _shade_color(hex_color: str, factor: float):
    """按 factor 缩放 #rrggbb 亮度，返回 QColor。"""
    try:
        c = QColor(hex_color)
    except Exception:
        c = QColor("#444444")
    if not c.isValid():
        c = QColor("#444444")
    r = min(255, max(0, round(c.red() * factor)))
    g = min(255, max(0, round(c.green() * factor)))
    b = min(255, max(0, round(c.blue() * factor)))
    return QColor(r, g, b)


def _squarify(items, x, y, w, h):
    """Squarified treemap 布局（移植自原 Canvas 版 JS 算法）。
    返回 [(node, rx, ry, rw, rh), ...]，坐标为浮点像素。"""
    out = []

    def layout(items, x, y, w, h):
        if not items or w <= 0 or h <= 0:
            return
        if len(items) == 1:
            out.append((items[0], x, y, w, h))
            return
        subtotal = sum(it["s"] for it in items)
        if subtotal <= 0:
            return
        short = min(w, h)
        rect_area = w * h

        def area_of(it):
            return (it["s"] / subtotal) * rect_area

        row = [items[0]]
        row_sum = items[0]["s"]
        rest = items[1:]

        def worst(row_items, rs):
            row_area = (rs / subtotal) * rect_area
            length = row_area / short
            if length <= 0:
                return float("inf")
            side_max = max(area_of(it) for it in row_items)
            side_min = min(area_of(it) for it in row_items)
            if side_min <= 0:
                return float("inf")
            return max((side_max / length) / short, short / (side_min / length))

        i = 0
        while i < len(rest):
            nxt = rest[i]
            if worst(row + [nxt], row_sum + nxt["s"]) > worst(row, row_sum):
                break
            row.append(nxt)
            row_sum += nxt["s"]
            i += 1

        remaining = rest[i:]
        row_frac = row_sum / subtotal
        if w <= h:
            rh = row_frac * h
            cx = x
            for it in row:
                cw = (it["s"] / row_sum) * w
                out.append((it, cx, y, cw, rh))
                cx += cw
            layout(remaining, x, y + rh, w, h - rh)
        else:
            rw = row_frac * w
            cy = y
            for it in row:
                ch = (it["s"] / row_sum) * h
                out.append((it, x, cy, rw, ch))
                cy += ch
            layout(remaining, x + rw, y, w - rw, h)

    layout(items, x, y, w, h)
    return out


class TreemapView(QWidget):
    """原生 QPainter Treemap 视图（替代原 QWebEngineView Canvas）。
    状态：idle 空状态 / loading 扫描动画 / tree 数据展示。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self._state = "idle"
        self._tree = []
        self._used_total = 1.0
        self._loading_letter = ""
        self._hit = []
        self._spin_deg = 0
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(90)
        self._spin_timer.timeout.connect(self._spin_tick)

    # ── 外部接口 ──────────────────────────────────────────────────
    def show_idle(self):
        self._state = "idle"
        self._tree = []
        self._spin_timer.stop()
        self.update()

    def show_loading(self, letter: str):
        self._state = "loading"
        self._loading_letter = letter
        self._tree = []
        self._spin_deg = 0
        self._spin_timer.start()
        self.update()

    def show_tree(self, tree, used_total):
        self._state = "tree"
        self._tree = tree or []
        self._used_total = float(used_total) if used_total and used_total > 0 else 1.0
        self._spin_timer.stop()
        self.update()

    def stop_loading(self):
        """关窗 / 切页时停止动画定时器。"""
        if self._state == "loading":
            self._spin_timer.stop()
            self._state = "idle"
            self.update()

    # ── 动画 ──────────────────────────────────────────────────────
    def _spin_tick(self):
        self._spin_deg = (self._spin_deg + 15) % 360
        self.update()

    # ── 绘制 ──────────────────────────────────────────────────────
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.fillRect(self.rect(), QColor(_treemap_bg()))
        self._hit = []
        w = self.width()
        h = self.height()
        if self._state == "loading":
            self._paint_spinner(p)
            self._paint_center_text(
                p, f"正在扫描 {self._loading_letter} 盘内容…", 13, tk("text_dim"), h / 2 - 8)
            self._paint_center_text(
                p, "最多等待 30 秒，超时会自动用已扫描到的结果显示", 11, tk("text_faint"), h / 2 + 18)
        elif self._state == "idle":
            self._paint_center_text(
                p, "点击上方「开始扫描」查看空间分布", 13, tk("text_faint"), h / 2)
        elif self._tree:
            rects = _squarify(self._tree, 0, 0, w, h)
            for (node, rx, ry, rw, rh) in rects:
                self._draw_node(p, node, rx, ry, rw, rh, 0, [])
        p.end()

    def _paint_center_text(self, p, text, pixel_size, color_hex, baseline_y):
        font = QFont("Microsoft YaHei")
        font.setPixelSize(pixel_size)
        p.setFont(font)
        p.setPen(QColor(color_hex))
        fm = QFontMetrics(font)
        tw = fm.horizontalAdvance(text)
        p.drawText(round((self.width() - tw) / 2), round(baseline_y), text)

    def _paint_spinner(self, p):
        p.save()
        p.translate(round(self.width() / 2), round(self.height() / 2 - 42))
        p.rotate(self._spin_deg)
        p.setPen(QPen(QColor(tk("border")), 3, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(QRectF(-16, -16, 32, 32), 0, 360 * 16)
        p.setPen(QPen(QColor(tk("accent")), 3, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(QRectF(-16, -16, 32, 32), 0, 100 * 16)
        p.restore()

    def _draw_node(self, p, node, x, y, w, h, depth, path):
        if w < 1 or h < 1:
            return
        px = round(x)
        py = round(y)
        pw = max(1, round(w))
        ph = max(1, round(h))

        shade_factor = 1 - min(depth * 0.05, 0.22)
        p.fillRect(px, py, pw, ph, _shade_color(node.get("c") or "#444444", shade_factor))

        p.fillRect(px, py, pw, 1, QColor(255, 255, 255, 26))
        p.fillRect(px, py, 1, ph, QColor(255, 255, 255, 18))
        p.fillRect(px, py + ph - 1, pw, 1, QColor(0, 0, 0, 140))
        p.fillRect(px + pw - 1, py, 1, ph, QColor(0, 0, 0, 102))

        cur_path = path + [node["n"]]
        self._hit.append({
            "x": px, "y": py, "w": pw, "h": ph,
            "node": node, "depth": depth, "path": cur_path,
        })

        children = node.get("ch") or []
        show_label = pw >= 26 and ph >= 14
        label_h = 0
        if show_label:
            label_h = 15 if depth == 0 else 13

        if children and pw > 12 and ph > 12:
            ip = 1
            ix = px + ip
            iy = py + ip + label_h
            iw = pw - ip * 2
            ih = ph - ip * 2 - label_h
            if iw > 4 and ih > 4:
                for (ch_node, rx, ry, rw2, rh2) in _squarify(children, ix, iy, iw, ih):
                    self._draw_node(p, ch_node, rx, ry, rw2, rh2, depth + 1, cur_path)

        if show_label:
            fs = max(8, min(12 if depth == 0 else 10, pw // 7))
            font = QFont("Microsoft YaHei")
            font.setPixelSize(fs)
            font.setWeight(QFont.DemiBold if depth == 0 else QFont.Medium)
            p.setFont(font)
            fm = QFontMetrics(font)
            max_w = pw - 6
            text = str(node["n"])
            if fm.horizontalAdvance(text) > max_w:
                text = fm.elidedText(text, Qt.ElideRight, max_w)
            tx = px + 3
            ty = py + 2 + fs * 0.82
            p.setPen(QColor(0, 0, 0, 140))
            p.drawText(tx + 1, round(ty) + 1, text)
            p.setPen(QColor(255, 255, 255, 235))
            p.drawText(tx, round(ty), text)


# ----------------------------------------------------------------------
# 顶层组件：磁盘空间分析（紧凑盘符 Tab + 原生 QPainter Treemap）
# ----------------------------------------------------------------------
class _ScanWorker(QThread):
    """后台线程执行耗时的目录扫描，避免大盘符扫描时界面卡死"""
    finished_scan = pyqtSignal(str, list, list)  # (drive_letter, tree, largest_files)
    progress = pyqtSignal(str, int, int, str)  # (drive_letter, done, total, current_name)

    def __init__(self, letter, root_path, used_gb, parent=None):
        super().__init__(parent)
        self.letter = letter
        self.root_path = root_path
        self.used_gb = used_gb

    def run(self):
        def _on_progress(done, total, name):
            self.progress.emit(self.letter, done, total, name)

        try:
            tree, largest = scan_drive_combined(
                self.root_path, used_total_gb=self.used_gb, progress_cb=_on_progress
            )
        except Exception:
            tree, largest = [], []
        self.finished_scan.emit(self.letter, tree, largest)


class _FileRankRow(QWidget):
    """
    大文件排行榜的单行展示：两行布局——
    第一行「序号 + 文件名」（超长自动省略号），
    第二行右对齐显示文件大小。
    避免之前文件名一长就把大小信息挤出可视区域的问题。
    """
    def __init__(self, rank, name, size_text, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(2)

        self.setAttribute(Qt.WA_StyledBackground, True)

        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        self.lbl_rank = QLabel(f"{rank}")
        self.lbl_rank.setFixedWidth(18)
        self.lbl_name = QLabel(name)
        self.lbl_name.setWordWrap(False)
        fm = QFontMetrics(self.lbl_name.font())
        elided = fm.elidedText(name, Qt.ElideMiddle, 220)
        self.lbl_name.setText(elided)
        name_row.addWidget(self.lbl_rank, 0)
        name_row.addWidget(self.lbl_name, 1)
        lay.addLayout(name_row)

        size_row = QHBoxLayout()
        size_row.setContentsMargins(18, 0, 0, 0)
        self.lbl_size = QLabel(size_text)
        size_row.addStretch(1)
        size_row.addWidget(self.lbl_size, 0)
        lay.addLayout(size_row)

        self.refresh_theme()

    def refresh_theme(self, *_):
        self.setStyleSheet(fmt(FILE_RANK_ROW_QSS))
        self.lbl_rank.setStyleSheet(fmt(FILE_RANK_NUM_QSS))
        self.lbl_name.setStyleSheet(fmt(FILE_RANK_NAME_QSS))
        self.lbl_size.setStyleSheet(fmt(FILE_RANK_SIZE_QSS))


class DiskAnalyzerWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        apply_transparent_surface(self, "DiskAnalyzerWidget")
        self.drives_data = []
        self.current_idx = 0
        self.tabs = []
        self._scan_worker = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 盘符 Tab 栏 + 扫描按钮/进度条（同一行，紧凑）──
        self.tab_bar = QWidget()
        self.tab_bar.setObjectName("DiskTabBar")
        tab_h = QHBoxLayout(self.tab_bar)
        tab_h.setContentsMargins(4, 0, 4, 0)
        tab_h.setSpacing(2)
        tab_h.setAlignment(Qt.AlignLeft)
        self.tab_h = tab_h
        root.addWidget(self.tab_bar, 0)

        # ── 扫描控制条：开始扫描按钮 + 进度条 + 状态文字 ──
        self.scan_bar = QWidget()
        scan_h = QHBoxLayout(self.scan_bar)
        scan_h.setContentsMargins(12, 8, 12, 8)
        scan_h.setSpacing(10)

        self.btn_scan = QPushButton("▶ 开始扫描")
        self.btn_scan.setCursor(Qt.PointingHandCursor)
        self.btn_scan.setFixedHeight(30)
        self.btn_scan.clicked.connect(self._on_scan_clicked)
        scan_h.addWidget(self.btn_scan, 0)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        scan_h.addWidget(self.progress_bar, 1)

        self.lbl_scan_status = QLabel("点击开始扫描该盘符")
        self.lbl_scan_status.setMinimumWidth(120)
        self.lbl_scan_status.setMaximumWidth(200)
        scan_h.addWidget(self.lbl_scan_status, 0)

        root.addWidget(self.scan_bar, 0)

        # ── Treemap + 大文件排行榜（左右布局，参考 WizTree 的分栏） ──
        body = QWidget()
        apply_transparent_surface(body, "DiskTreemapBody")
        body_h = QHBoxLayout(body)
        body_h.setContentsMargins(0, 0, 0, 0)
        body_h.setSpacing(0)

        self.treemap = TreemapView()
        self.treemap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        body_h.addWidget(self.treemap, 1)

        # 大文件排行榜：窄窗时让出 Treemap 主区（原固定 300 在 ~700 内容宽下过宽）
        self.files_panel = QWidget()
        self.files_panel.setFixedWidth(220)
        fp_lay = QVBoxLayout(self.files_panel)
        fp_lay.setContentsMargins(8, 8, 8, 8)
        fp_lay.setSpacing(4)

        fp_title = QLabel("最大文件 TOP 15")
        self.fp_title = fp_title
        fp_lay.addWidget(fp_title)

        self.files_list = QListWidget()
        self.files_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.files_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.files_list.setSpacing(2)
        fp_lay.addWidget(self.files_list, 1)

        body_h.addWidget(self.files_panel, 0)
        root.addWidget(body, 1)

        # 首次上色 + 订阅主题切换
        self.refresh_theme()
        theme.changed.connect(self.refresh_theme)

        # 初始空状态：不自动扫描，等待用户点击按钮
        self.treemap.show_idle()

        QTimer.singleShot(0, self.reload_drives)

    # ------------------------------------------------------------------
    def refresh_theme(self, *_):
        """重刷全部控件级样式。只 setStyleSheet，不重建控件，不丢扫描状态。"""
        self.tab_bar.setStyleSheet(fmt(TAB_BAR_QSS))
        self.scan_bar.setStyleSheet(fmt(SCAN_BAR_QSS))
        self.btn_scan.setStyleSheet(fmt(SCAN_BTN_QSS))
        self.progress_bar.setStyleSheet(fmt(SCAN_PROGRESS_QSS))
        self.lbl_scan_status.setStyleSheet(fmt(SCAN_STATUS_QSS))
        self.files_panel.setStyleSheet(fmt(FILES_PANEL_QSS))
        self.fp_title.setStyleSheet(fmt(FILES_TITLE_QSS))
        self.files_list.setStyleSheet(fmt(FILES_LIST_QSS))

        # 动态子控件：盘符 Tab 与文件排行条目
        for tab in self.tabs:
            tab.refresh_theme()
        for i in range(self.files_list.count()):
            row = self.files_list.itemWidget(self.files_list.item(i))
            if row is not None and hasattr(row, "refresh_theme"):
                row.refresh_theme()

        # Treemap 原生控件：主题色在 paintEvent 时读取，只需重绘
        self.treemap.update()

    def reload_drives(self):
        """扫描真实磁盘；失败则用演示数据兜底，保证界面始终可预览"""
        real = scan_drives()
        if real:
            for d in real:
                d["tree"] = None  # 懒加载
            self.drives_data = real
        else:
            self.drives_data = DEMO_DRIVES

        for t in self.tabs:
            t.setParent(None)
        self.tabs = []

        for i, d in enumerate(self.drives_data):
            tab = DriveTab(d["letter"], d["used"], d["total"])
            tab.mousePressEvent = (lambda e, idx=i: self._select(idx))
            self.tab_h.addWidget(tab)
            self.tabs.append(tab)
        self.tab_h.addStretch(1)

        if self.drives_data:
            self._select(0)

    def _select(self, idx):
        if idx < 0 or idx >= len(self.drives_data):
            return
        self.current_idx = idx
        for i, t in enumerate(self.tabs):
            t.set_active(i == idx)

        d = self.drives_data[idx]
        tree = d.get("tree")

        if tree is not None:
            # 已扫描过，直接渲染缓存结果，无需重新扫描
            self.treemap.show_tree(tree, d["used"])
            self._render_files_list(d.get("largest_files") or [])
            self._set_scan_ui_idle(scanned=True)
            return

        # 尚未扫描：显示空状态，等待用户点击"开始扫描"按钮
        self.treemap.show_idle()
        self.files_list.clear()
        self._set_scan_ui_idle(scanned=False)

    def _set_scan_ui_idle(self, scanned: bool):
        """重置扫描控制条为初始状态（按钮可点、进度条归零）"""
        self.btn_scan.setEnabled(True)
        self.btn_scan.setText("🔄 重新扫描" if scanned else "▶ 开始扫描")
        self.progress_bar.setValue(0)
        self.lbl_scan_status.setText("已扫描，可重新扫描" if scanned else "点击开始扫描该盘符")

    def _on_scan_clicked(self):
        if not self.drives_data or self.current_idx >= len(self.drives_data):
            return
        d = self.drives_data[self.current_idx]
        used = d["used"]

        self.btn_scan.setEnabled(False)
        self.btn_scan.setText("扫描中…")
        self.progress_bar.setValue(0)
        self.lbl_scan_status.setText("正在枚举目录…")

        self.treemap.show_loading(d["letter"])
        self.files_list.clear()

        if os.name != "nt":
            fallback = next((dd for dd in DEMO_DRIVES if dd["letter"] == d["letter"]), None)
            tree = fallback["tree"] if fallback else []
            d["tree"] = tree
            d["largest_files"] = DEMO_LARGEST_FILES
            self.treemap.show_tree(tree, used)
            self._render_files_list(DEMO_LARGEST_FILES)
            self._set_scan_ui_idle(scanned=True)
            return

        # 若已有扫描线程在跑，不调用 terminate()（在 Windows 上可能让线程
        # 停在不安全状态），断开旧信号即可，让它自然跑完被丢弃
        if self._scan_worker is not None and self._scan_worker.isRunning():
            try:
                self._scan_worker.finished_scan.disconnect()
                self._scan_worker.progress.disconnect()
            except Exception:
                pass

        root_path = d["letter"] + "\\"
        worker = _ScanWorker(d["letter"], root_path, used, parent=self)
        worker.finished_scan.connect(self._on_scan_finished)
        worker.progress.connect(self._on_scan_progress)
        self._scan_worker = worker
        worker.start()

    def shutdown(self):
        """关窗：断开信号并等待扫描线程（尽量不 terminate）。"""
        from utils.qthread_util import stop_qthread
        self.treemap.stop_loading()
        w = getattr(self, "_scan_worker", None)
        if w is not None and w.isRunning():
            try:
                w.finished_scan.disconnect()
            except Exception:
                pass
            try:
                w.progress.disconnect()
            except Exception:
                pass
        stop_qthread(w, timeout_ms=3000, name="disk-scan")

    def _on_scan_progress(self, letter, done, total, name):
        # 进度回调可能来自已经切走的盘符的旧线程，需要核对
        if not self.drives_data or self.current_idx >= len(self.drives_data):
            return
        if self.drives_data[self.current_idx]["letter"] != letter:
            return
        pct = int(done / total * 100) if total else 0
        self.progress_bar.setValue(pct)
        if name:
            self.lbl_scan_status.setText(f"正在扫描：{name}（{done}/{total}）")
        else:
            self.lbl_scan_status.setText("整理结果中…")

    def _render_files_list(self, files):
        """把最大文件排行榜渲染进侧栏列表：两行卡片样式
        （第一行文件名，第二行右对齐大小），避免文件名过长时把大小挤掉。"""
        self.files_list.clear()
        if not files:
            placeholder = QListWidgetItem("暂无数据")
            self.files_list.addItem(placeholder)
            return
        for i, f in enumerate(files, 1):
            item = QListWidgetItem()
            self.files_list.addItem(item)
            row = _FileRankRow(i, f["name"], fmt_size(f["size_gb"]))
            item.setSizeHint(row.sizeHint())
            self.files_list.setItemWidget(item, row)

    def _on_scan_finished(self, letter, tree, largest_files):
        # 扫描完成时用户可能已经切换到其他盘符，需要核对当前选中的仍是这个盘符
        d = self.drives_data[self.current_idx] if self.drives_data else None
        is_current = d is not None and d["letter"] == letter

        target = d if is_current else next((dd for dd in self.drives_data if dd["letter"] == letter), None)
        if target is None:
            return

        if not tree:
            fallback = next((dd for dd in DEMO_DRIVES if dd["letter"] == letter), None)
            tree = fallback["tree"] if fallback else []

        target["tree"] = tree
        target["largest_files"] = largest_files

        if not is_current:
            # 不是当前显示的盘符，仅缓存结果，不刷新画面
            return

        self.treemap.show_tree(tree, target["used"])
        self._render_files_list(largest_files)
        self._set_scan_ui_idle(scanned=True)
