# -*- coding: utf-8 -*-
"""
page_image_proc.py  —  图片处理

侧栏「助手 · 主手脚」：粘贴助手 与 积分计算 之间。

布局（比例锁定，随窗口缩放，分区不可拖拽改）：
  · 上 60% / 下 40%
  · 上：左 70%（拖放提示+选图 / 预览） / 右 30%（缩小 · 放大 · 切割 · 更多修改；点中激活，其余冻结；底栏格式+保存）
  · 下：全宽「输出预览」——纵向滚动图片卡（文件名 / 分辨率·大小同行），单击打开输出目录

输出目录：公共保存根下 ImageProc；偏好：records/user.txt → image_proc 段。
JPG 固定最高品质（quality=100），无品质控件。
"""

from __future__ import annotations

import io
import os
import re
import subprocess
from datetime import datetime
from typing import List, Optional, Tuple

from PyQt5.QtCore import Qt, QSize, QUrl, QTimer, pyqtSignal, QThread, QRectF, QPointF, QEvent
from PyQt5.QtGui import (
    QPixmap, QImage, QDesktopServices, QDragEnterEvent, QDropEvent,
    QPainter, QColor, QPen, QBrush, QPainterPath,
)
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog,
    QRadioButton, QButtonGroup, QSizePolicy, QApplication, QScrollArea,
    QFrame, QGridLayout, QLayout,
)

from styles.style_all import (
    install_card_title,
    restyle_card_title,
    restyle_card_frame,
    make_card,
    apply_medium_button,
    apply_btn_download,
    theme,
    tk,
    CARD_LEFT_GAP,
    CARD_TOP_GAP,
    CARD_RIGHT_GAP,
    CARD_BOTTOM_GAP,
    CARD_RADIUS,
    LIVE_BUTTON_H,
    sp,
)
from utils.flow_layout import FlowLayout
from utils.logger import get_logger
from utils.file_utils import ensure_dir

log = get_logger(__name__)

# 支持的输入扩展
_IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
_IMG_FILTER = "图片 (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff *.gif);;所有文件 (*.*)"

# JPG 最高品质（本页凡写 JPG 统一使用，不暴露滑条）
_JPG_QUALITY = 100
_JPG_SUBSAMPLING = 0  # 4:4:4

# Upscayl / Real-ESRGAN（ncnn-vulkan）：6 个 4× 模型，按钮六选一
# id = model/upscayl 下 .bin/.param 的文件名（无后缀）
# 第四项：输出文件名里的「操作方式」（与缩小/切割并列）
_UPSCAYL_MODELS = (
    ("realesrgan-x4plus", "提高清晰度", "提高清晰度 · REAL-ESRGAN", "放大清晰"),
    ("realesrgan-x4fast", "快速模式", "快速模式 · FAST REAL-ESRGAN", "放大快速"),
    ("remacri", "增强效果", "增强效果 · REMACRI", "放大增强"),
    ("ultramix_balanced", "提高饱和度", "提高色彩饱和度 · ULTRAMIXBALANCED", "放大饱和"),
    ("ultrasharp", "清晰锐化", "提高清晰度和边缘锐化 · ULTRASHARP", "放大锐化"),
    ("realesrgan-x4plus-anime", "数字艺术", "数字艺术-提高颜色和纹理细节 · DIGITALART", "放大数字艺术"),
)
_UPSCAYL_FILE_TAG = {m[0]: m[3] for m in _UPSCAYL_MODELS}

_SPLIT_FILE_TAGS = ("切割左上", "切割右上", "切割左下", "切割右下")
_UPSCAYL_DEFAULT = "realesrgan-x4plus"
_UPSCAYL_DIRECT_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_UPSCAYL_IDS = {m[0] for m in _UPSCAYL_MODELS}
_UPSCAYL_SCALE_MIN = 2
_UPSCAYL_SCALE_MAX = 6
_UPSCAYL_SCALE_DEFAULT = 4
_OP_KEYS = ("scale", "upscale", "split", "more")
_OP_STATUS_NAMES = {
    "scale": "缩小",
    "upscale": "放大",
    "split": "切割",
    "more": "修改",
}
_OP_STATUS_MAX = 10

# 版面锁定比例（窗口缩放时保持，分区不可拖拽改）
_LAYOUT_LEFT_FRAC = 0.70  # 左上 : 右上 = 7 : 3
_LAYOUT_TOP_FRAC = 0.60   # 上 : 下 = 3 : 2
_LAYOUT_GAP = 8


def _has_pillow() -> bool:
    try:
        from PIL import Image  # noqa: F401
        return True
    except Exception:
        return False


def _unique_path(dir_path: str, base: str, ext: str) -> str:
    """dir/base.ext，重名则 base_1.ext、base_2…（临时文件 / 剪贴板用）。"""
    ext = ext if ext.startswith(".") else f".{ext}"
    candidate = os.path.join(dir_path, f"{base}{ext}")
    if not os.path.exists(candidate):
        return candidate
    n = 1
    while True:
        candidate = os.path.join(dir_path, f"{base}_{n}{ext}")
        if not os.path.exists(candidate):
            return candidate
        n += 1


def _src_stem(path: str) -> str:
    """当前图的「原名」（无扩展名）；去掉 Windows 非法字符。"""
    name = os.path.splitext(os.path.basename(path or ""))[0]
    name = (name or "image").strip()
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    return name or "image"


def _max_op_version(dir_path: str, stem: str, date: str, ops, ext: str) -> int:
    """已有 {stem}_{date}_{op}_{NN} 的最大版本号；没有则 0。"""
    ext_l = (ext if str(ext).startswith(".") else f".{ext}").lower()
    if isinstance(ops, str):
        ops = (ops,)
    prefixes = [f"{stem}_{date}_{op}_" for op in ops]
    max_n = 0
    try:
        names = os.listdir(dir_path)
    except OSError:
        return 0
    for name in names:
        base, e = os.path.splitext(name)
        if e.lower() != ext_l:
            continue
        for prefix in prefixes:
            if not base.startswith(prefix):
                continue
            tail = base[len(prefix):]
            if tail.isdigit():
                try:
                    max_n = max(max_n, int(tail))
                except (TypeError, ValueError):
                    pass
            break
    return max_n


def _op_save_path(dir_path: str, stem: str, op: str, ext: str, ver: int = None) -> str:
    """原名_日期_操作方式_版本号（版本从 01 起）。ver 给定则用该号。"""
    date = datetime.now().strftime("%Y%m%d")
    ext = ext if str(ext).startswith(".") else f".{ext}"
    if ver is None:
        ver = _max_op_version(dir_path, stem, date, (op,), ext) + 1
    if ver < 1:
        ver = 1
    return os.path.join(dir_path, f"{stem}_{date}_{op}_{ver:02d}{ext}")


def _open_pil(path: str):
    from PIL import Image, ImageOps
    img = Image.open(path)
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        log.debug("EXIF 方向校正失败 path=%s", path, exc_info=True)
    return img


def _to_rgb_for_jpg(img):
    from PIL import Image
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def _save_jpg(img, path: str) -> None:
    rgb = _to_rgb_for_jpg(img)
    rgb.save(
        path,
        format="JPEG",
        quality=_JPG_QUALITY,
        subsampling=_JPG_SUBSAMPLING,
        optimize=True,
    )


def _save_png(img, path: str) -> None:
    if img.mode not in ("RGB", "RGBA", "L", "LA", "P"):
        img = img.convert("RGBA")
    img.save(path, format="PNG", optimize=True)


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.2f} MB"


def _read_image_meta(path: str) -> tuple:
    """返回 (w, h, size_bytes)。"""
    size = 0
    try:
        size = os.path.getsize(path)
    except OSError:
        pass
    w = h = 0
    try:
        pm = QPixmap(path)
        if not pm.isNull():
            w, h = pm.width(), pm.height()
    except Exception:
        pass
    if w <= 0 or h <= 0:
        try:
            img = _open_pil(path)
            w, h = img.size
            img.close()
        except Exception:
            pass
    return w, h, size


def _pil_to_qpixmap(img) -> QPixmap:
    """把 Pillow 图转成 QPixmap，供未落盘的预览使用。"""
    tmp = img
    if tmp.mode not in ("RGB", "RGBA"):
        tmp = tmp.convert("RGBA")
    buf = io.BytesIO()
    tmp.save(buf, format="PNG")
    pm = QPixmap()
    pm.loadFromData(buf.getvalue())
    return pm


def _pil_transpose_flag(name: str):
    from PIL import Image as PILImage
    src = getattr(PILImage, "Transpose", PILImage)
    return getattr(src, name)


def _upscayl_model_ok(folder: str, name: str) -> bool:
    if not folder or not name:
        return False
    return (
        os.path.isfile(os.path.join(folder, name + ".bin"))
        and os.path.isfile(os.path.join(folder, name + ".param"))
    )


def _upscayl_exe_and_models() -> Tuple[str, str]:
    """返回 (upscayl-bin.exe, 模型目录)。优先项目 model/upscayl，其次本机 Upscayl 安装目录。"""
    local = ""
    try:
        from utils.app_paths import model_file
        local = model_file("upscayl")
    except Exception:
        local = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "model", "upscayl",
        )
    pf_exe = os.path.join(
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        "Upscayl", "resources", "bin", "upscayl-bin.exe",
    )
    pf_models = os.path.join(
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        "Upscayl", "resources", "models",
    )
    local_exe = os.path.join(local, "upscayl-bin.exe") if local else ""
    local_models = os.path.join(local, "models") if local else ""
    pairs = (
        (local_exe, local_models),
        (local_exe, local),
        (pf_exe, local_models),
        (pf_exe, pf_models),
    )
    for exe, models in pairs:
        if exe and os.path.isfile(exe) and _upscayl_model_ok(models, _UPSCAYL_DEFAULT):
            return exe, models
    return local_exe if os.path.isfile(local_exe) else "", local or ""


class _UpscaylWorker(QThread):
    """调用 upscayl-bin 做超分；-z 固定模型 4×，-s 为输出倍数 2–6。"""

    progress = pyqtSignal(int)
    finished_ok = pyqtSignal(str)
    finished_err = pyqtSignal(str)

    def __init__(
        self, exe: str, model_dir: str, model_name: str, src: str, dst: str,
        scale: int = 4,
    ):
        super().__init__()
        self._exe = exe
        self._model_dir = model_dir
        self._model_name = model_name
        self._src = src
        self._dst = dst
        try:
            self._scale = max(_UPSCAYL_SCALE_MIN, min(_UPSCAYL_SCALE_MAX, int(scale)))
        except (TypeError, ValueError):
            self._scale = _UPSCAYL_SCALE_DEFAULT
        self._cancel = False
        self._proc = None

    def stop(self):
        self._cancel = True
        try:
            self.requestInterruption()
        except Exception:
            pass
        proc = self._proc
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass

    def run(self):
        cmd = [
            self._exe,
            "-i", self._src,
            "-o", self._dst,
            "-m", self._model_dir,
            "-n", self._model_name,
            "-z", "4",
            "-s", str(self._scale),
            "-f", "png",
        ]
        env = os.environ.copy()
        exe_dir = os.path.dirname(self._exe)
        env["PATH"] = exe_dir + os.pathsep + env.get("PATH", "")
        try:
            kw = dict(
                args=cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=exe_dir or None,
            )
            if os.name == "nt":
                kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            self._proc = subprocess.Popen(**kw)
            pct_re = re.compile(rb"(\d+(?:\.\d+)?)\s*%")
            last = -1
            buf = b""
            while True:
                if self._cancel or self.isInterruptionRequested():
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
                    return
                chunk = self._proc.stdout.read(256) if self._proc.stdout else b""
                if not chunk:
                    break
                buf += chunk
                while True:
                    m = pct_re.search(buf)
                    if not m:
                        if len(buf) > 4096:
                            buf = buf[-512:]
                        break
                    try:
                        pct = int(float(m.group(1)))
                    except (TypeError, ValueError):
                        pct = last
                    buf = buf[m.end():]
                    if pct != last:
                        last = pct
                        self.progress.emit(max(0, min(100, pct)))
            code = self._proc.wait()
        except Exception as e:
            if not self._cancel:
                self.finished_err.emit("%s: %s" % (type(e).__name__, e))
            return
        if self._cancel:
            return
        ok_file = os.path.isfile(self._dst) and os.path.getsize(self._dst) >= 32
        if code != 0 or not ok_file:
            self.finished_err.emit("放大失败（退出码 %s）" % code)
            return
        self.progress.emit(100)
        self.finished_ok.emit(self._dst)


# ── 省略标签（对齐抖音页） ─────────────────────────────────────────────────
class _ElideLabel(QLabel):
    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._full = text or ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def setFullText(self, text: str):
        self._full = text or ""
        self._apply_elide()

    def _apply_elide(self):
        fm = self.fontMetrics()
        avail = max(0, self.width() - 2)
        super().setText(fm.elidedText(self._full, Qt.ElideRight, avail))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_elide()


# ── 输出图片卡（参考抖音 MediaCard：缩略图 + 信息行） ──────────────────────
def _format_badge_text(path: str) -> str:
    """由扩展名得到类型标识：JPG / PNG；其它取大写扩展名（最多 4 字）。"""
    ext = os.path.splitext(path or "")[1].lower().lstrip(".")
    if ext in ("jpg", "jpeg"):
        return "JPG"
    if ext == "png":
        return "PNG"
    if not ext:
        return "?"
    return ext.upper()[:4]


class OutputImageCard(QFrame):
    """输出预览卡：完整缩略图 + 文件名 / 分辨率·体积同行；单击打开目录。"""

    THUMB_W = 120
    THUMB_H = 90
    LINE_H = 16
    # 卡宽略大于缩略图，保证「3840×2160 · 12.34 MB」一行能放下
    CARD_W = 140
    CARD_H = THUMB_H + 4 + LINE_H * 2 + 6
    BADGE_W = 33
    BADGE_H = 16
    BADGE_MARGIN = 4  # 距缩略图右/下边距

    clicked_open = pyqtSignal(str)  # 文件路径

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        self.setFixedSize(self.CARD_W, self.CARD_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_StyledBackground, True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)

        # 缩略图容器：底图 + 右下角类型标签叠层
        self.thumb_host = QWidget()
        self.thumb_host.setFixedSize(self.THUMB_W, self.THUMB_H)
        self.thumb_host.setAttribute(Qt.WA_StyledBackground, True)
        self.thumb_host.setAttribute(Qt.WA_TranslucentBackground, True)
        self.thumb_host.setAutoFillBackground(False)

        self.thumb = QLabel("?", self.thumb_host)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setAttribute(Qt.WA_TranslucentBackground, True)
        self.thumb.setAutoFillBackground(False)
        self.thumb.setGeometry(0, 0, self.THUMB_W, self.THUMB_H)

        self.lbl_fmt = QLabel(_format_badge_text(path), self.thumb_host)
        self.lbl_fmt.setObjectName("ImgProcFmtBadge")
        self.lbl_fmt.setFixedSize(self.BADGE_W, self.BADGE_H)
        self.lbl_fmt.setAlignment(Qt.AlignCenter)
        self.lbl_fmt.move(
            self.THUMB_W - self.BADGE_W - self.BADGE_MARGIN,
            self.THUMB_H - self.BADGE_H - self.BADGE_MARGIN,
        )
        self.lbl_fmt.raise_()
        lay.addWidget(self.thumb_host, 0, Qt.AlignHCenter)

        name = os.path.basename(path)
        w, h, nbytes = _read_image_meta(path)
        res = f"{w}×{h}" if w and h else "—×—"
        size_s = _fmt_size(nbytes)

        self.lbl_name = _ElideLabel(name)
        self.lbl_name.setFullText(name)
        self.lbl_name.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self.lbl_name.setFixedHeight(self.LINE_H)
        lay.addWidget(self.lbl_name)

        self.lbl_info = QLabel(f"{res} · {size_s}")
        self.lbl_info.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self.lbl_info.setFixedHeight(self.LINE_H)
        lay.addWidget(self.lbl_info)

        self.refresh_theme()
        self._load_thumb(path)

    def refresh_theme(self, *_):
        self.setStyleSheet("QFrame{background:transparent;border:none;}")
        try:
            self.thumb_host.setStyleSheet(
                "background:transparent;border:none;"
            )
        except Exception:
            pass
        self.thumb.setStyleSheet(
            f"font-size:28px;background:transparent;border:none;"
            f"color:{tk('text_mut')};"
        )
        self.lbl_name.setStyleSheet(
            f"color:{tk('text')};font-size:11px;font-weight:600;"
            f"background:transparent;border:none;"
        )
        # 分辨率 · 大小：比 text_mut 提亮，贴近正文色
        try:
            meta_color = tk("text")
        except Exception:
            meta_color = "#e2e8f0"
        self.lbl_info.setStyleSheet(
            f"color:{meta_color};font-size:10px;"
            f"background:transparent;border:none;"
        )
        self._style_fmt_badge()

    def _style_fmt_badge(self):
        """类型标签：33×16、1px 描边；PNG 亮绿 / JPG 亮蓝，字号保持 11。"""
        lbl = getattr(self, "lbl_fmt", None)
        if lbl is None:
            return
        try:
            is_dark = bool(theme.is_dark)
        except Exception:
            is_dark = True
        kind = (lbl.text() or "").strip().upper()
        if kind == "PNG":
            accent = "#4ade80"  # 亮绿
        elif kind == "JPG":
            accent = "#60a5fa"  # 亮蓝
        else:
            try:
                accent = tk("text_mut")
            except Exception:
                accent = "#94a3b8"
        # 半透明底，保证压在任意缩略图上仍可读
        if is_dark:
            bg = "rgba(15, 23, 42, 0.78)"
        else:
            bg = "rgba(255, 255, 255, 0.88)"
        lbl.setStyleSheet(
            f"QLabel#ImgProcFmtBadge{{"
            f"color:{accent};font-size:11px;font-weight:700;"
            f"background:{bg};border:1px solid {accent};"
            f"border-radius:3px;padding:0;}}"
        )

    def _load_thumb(self, path: str):
        src = QPixmap(path)
        if src.isNull():
            return
        scaled = src.scaled(
            self.THUMB_W, self.THUMB_H,
            Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        canvas = QPixmap(self.THUMB_W, self.THUMB_H)
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        x = max(0, (self.THUMB_W - scaled.width()) // 2)
        y = max(0, (self.THUMB_H - scaled.height()) // 2)
        painter.drawPixmap(x, y, scaled)
        painter.end()
        self.thumb.setPixmap(canvas)
        self.thumb.setText("")
        if getattr(self, "lbl_fmt", None) is not None:
            self.lbl_fmt.raise_()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked_open.emit(self.path)
            e.accept()
            return
        super().mousePressEvent(e)


# ── 左侧预览 ───────────────────────────────────────────────────────────────
# 操作键高度：live 标准高减 4px
_ACTION_BTN_H = max(18, int(LIVE_BUTTON_H) - 4)


def _apply_save_button(btn: QPushButton) -> QPushButton:
    """能写出图片的主操作：全站橙色（BtnDownload），高度与右侧操作键一致。"""
    apply_btn_download(btn)
    try:
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        btn.setFixedHeight(int(_ACTION_BTN_H))
        btn.setMinimumWidth(72)
        btn.setCursor(Qt.PointingHandCursor)
    except Exception:
        pass
    return btn


class _ImagePreview(QLabel):
    paths_dropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._src: Optional[QPixmap] = None
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(120, 120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAcceptDrops(True)
        self.setWordWrap(True)
        self._apply_style()
        self.show_empty()

    def hasHeightForWidth(self):
        return False

    def minimumSizeHint(self):
        return QSize(120, 120)

    def sizeHint(self):
        return QSize(240, 280)

    def refresh_theme(self, *_):
        self._apply_style()
        if self._src is None:
            self.show_empty()

    def _apply_style(self):
        self.setStyleSheet(
            "QLabel{background:transparent;border:none;padding:0;}"
        )

    def show_empty(self):
        self._src = None
        self.clear()
        self.setText("")

    def set_image_path(self, path: str) -> bool:
        pm = QPixmap(path)
        return self.set_pixmap(pm)

    def set_pixmap(self, pm: QPixmap) -> bool:
        if pm is None or pm.isNull():
            self.show_empty()
            return False
        self._src = pm
        self.setText("")
        self._rescale()
        return True

    def _rescale(self):
        if self._src is None:
            return
        avail = self.size() - QSize(16, 16)
        if avail.width() < 8 or avail.height() < 8:
            return
        self.setPixmap(
            self._src.scaled(avail, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._rescale()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData() and (e.mimeData().hasUrls() or e.mimeData().hasImage()):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e: QDropEvent):
        md = e.mimeData()
        if md is None:
            return
        if md.hasUrls():
            for url in md.urls():
                p = url.toLocalFile()
                if not p:
                    continue
                ext = os.path.splitext(p)[1].lower()
                if ext in _IMG_EXTS and os.path.isfile(p):
                    self.paths_dropped.emit(p)
                    e.acceptProposedAction()
                    return
        e.ignore()


class _ScaleSlider(QWidget):
    """2–6 五档滑条：轨道上 5 个点，当前倍数画在圆形把手上（如 4x）。"""

    valueChanged = pyqtSignal(int)

    HANDLE_D = 22  # 容纳 4x 等两字符；字号 11
    HANDLE_FONT = 11  # 原 9 + 2
    DOT_D = 6
    TRACK_H = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ImgProcUpscaleScale")
        self._min = _UPSCAYL_SCALE_MIN
        self._max = _UPSCAYL_SCALE_MAX
        self._value = _UPSCAYL_SCALE_DEFAULT
        self._drag = False
        self.setMinimumHeight(self.HANDLE_D + 4)
        self.setFixedHeight(self.HANDLE_D + 4)
        self.setMinimumWidth(self.HANDLE_D * 5 + 8)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)

    def value(self) -> int:
        return int(self._value)

    def setValue(self, v: int):
        try:
            n = int(v)
        except (TypeError, ValueError):
            n = _UPSCAYL_SCALE_DEFAULT
        n = max(self._min, min(self._max, n))
        if n == self._value:
            self.update()
            return
        self._value = n
        self.update()
        if not self.signalsBlocked():
            self.valueChanged.emit(n)

    def sizeHint(self):
        return QSize(180, self.HANDLE_D + 4)

    def _stops_x(self):
        steps = self._max - self._min
        r = self.HANDLE_D / 2.0
        left = r
        right = max(left, float(self.width()) - r)
        if steps <= 0:
            return [left]
        span = right - left
        return [left + span * i / steps for i in range(steps + 1)]

    def _value_at_x(self, x: float) -> int:
        xs = self._stops_x()
        best_i = 0
        best_d = 1e9
        for i, px in enumerate(xs):
            d = abs(px - x)
            if d < best_d:
                best_d = d
                best_i = i
        return self._min + best_i

    def paintEvent(self, _e):
        w, h = self.width(), self.height()
        if w <= 2 or h <= 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        try:
            bd = QColor(tk("border"))
            acc = QColor(tk("accent"))
            groov = QColor(tk("panel_deep"))
            mut = QColor(tk("text_mut"))
            is_dark = bool(theme.is_dark)
        except Exception:
            bd = QColor("#25345c")
            acc = QColor("#3a8ee0")
            groov = QColor("#080f1c")
            mut = QColor("#9fb0d7")
            is_dark = True
        handle_fg = QColor("#ffffff") if (is_dark or acc.lightness() < 160) else QColor("#0f172a")
        if not self.isEnabled():
            acc = mut
            handle_fg = mut

        cy = h / 2.0
        xs = self._stops_x()
        track_y = cy - self.TRACK_H / 2.0
        track = QRectF(xs[0], track_y, max(0.0, xs[-1] - xs[0]), self.TRACK_H)
        p.setPen(QPen(bd, 1))
        p.setBrush(QBrush(groov))
        p.drawRoundedRect(track, 2, 2)

        idx = self._value - self._min
        hx = xs[max(0, min(len(xs) - 1, idx))]
        if hx > xs[0]:
            filled = QRectF(xs[0], track_y, hx - xs[0], self.TRACK_H)
            p.setPen(QPen(acc, 1))
            p.setBrush(QBrush(acc))
            p.drawRoundedRect(filled, 2, 2)

        dr = self.DOT_D / 2.0
        for i, px in enumerate(xs):
            v = self._min + i
            if v == self._value:
                continue
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(acc if px <= hx else mut))
            p.drawEllipse(QPointF(px, cy), dr, dr)

        hr = self.HANDLE_D / 2.0
        p.setPen(QPen(acc.darker(110) if not is_dark else acc, 1))
        p.setBrush(QBrush(acc))
        p.drawEllipse(QPointF(hx, cy), hr, hr)

        font = self.font()
        font.setPixelSize(self.HANDLE_FONT)
        font.setBold(True)
        p.setFont(font)
        p.setPen(handle_fg)
        p.drawText(
            QRectF(hx - hr, cy - hr, float(self.HANDLE_D), float(self.HANDLE_D)),
            Qt.AlignCenter,
            "%sx" % self._value,
        )

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.isEnabled():
            self._drag = True
            self.grabMouse()
            self.setValue(self._value_at_x(e.x()))
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag and self.isEnabled():
            self.setValue(self._value_at_x(e.x()))
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._drag:
            self._drag = False
            try:
                self.releaseMouse()
            except Exception:
                pass
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if not self.isEnabled():
            super().keyPressEvent(e)
            return
        if e.key() in (Qt.Key_Left, Qt.Key_Down):
            self.setValue(self._value - 1)
            e.accept()
            return
        if e.key() in (Qt.Key_Right, Qt.Key_Up):
            self.setValue(self._value + 1)
            e.accept()
            return
        super().keyPressEvent(e)


class _OpGlass(QWidget):
    """盖在冻结功能卡上：灰色蒙层 + 点击激活。"""

    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ImgProcOpGlass")
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        if parent is not None:
            parent.installEventFilter(self)
            self.setGeometry(parent.rect())
            self.raise_()

    def eventFilter(self, obj, event):
        if obj is self.parent():
            t = event.type()
            if t in (QEvent.Resize, QEvent.Show):
                self.setGeometry(obj.rect())
                if self.isVisible():
                    self.raise_()
        return False

    def paintEvent(self, _e):
        w, h = self.width(), self.height()
        if w <= 2 or h <= 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        try:
            is_dark = bool(theme.is_dark)
        except Exception:
            is_dark = True
        if is_dark:
            fill = QColor(8, 14, 28, 168)
        else:
            fill = QColor(226, 232, 240, 176)
        try:
            r = float(sp(CARD_RADIUS))
        except Exception:
            r = float(CARD_RADIUS)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), r, r)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(fill))
        p.drawPath(path)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
            e.accept()
            return
        super().mousePressEvent(e)


class _QuietScroll(QScrollArea):
    """四选区：内容按自身高度，不压扁；滚轮可滑，永不画出滚动条。

    widgetResizable=True 时 Qt 会把内容压进视口来避免出现滚动条，
    六个放大按钮就会叠在一起。这里按内容算高、宽跟视口走。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(False)
        self.setFrameShape(QFrame.NoFrame)
        self.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollBar:vertical{width:0px;}"
            "QScrollBar:horizontal{height:0px;}"
        )

    def setWidget(self, w):
        old = self.widget()
        if old is not None:
            old.removeEventFilter(self)
        super().setWidget(w)
        if w is not None:
            w.setMinimumWidth(0)
            w.installEventFilter(self)
            self._sync()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync()

    def eventFilter(self, obj, event):
        if obj is self.widget() and event.type() in (
            QEvent.LayoutRequest, QEvent.Resize, QEvent.Show,
        ):
            QTimer.singleShot(0, self._sync)
        return super().eventFilter(obj, event)

    def _sync(self):
        inner = self.widget()
        if inner is None:
            return
        vw = max(1, self.viewport().width())
        inner.setMinimumWidth(0)
        inner.setMaximumWidth(16777215)
        hint = inner.sizeHint()
        minh = inner.minimumSizeHint()
        h = max(1, int(hint.height()), int(minh.height()))
        if inner.width() != vw or inner.height() != h:
            inner.setFixedSize(vw, h)


class _RatioRow(QWidget):
    """左右按固定比例切宽。用 setGeometry 摆放，避免 setFixedWidth 把 min=max 锁死。"""

    def __init__(self, left_frac: float = 0.7, spacing: int = 8, parent=None):
        super().__init__(parent)
        self._left_frac = float(left_frac)
        self._spacing = int(spacing)
        self._left = None
        self._right = None
        self._placing = False
        self.setMinimumWidth(0)
        self.setMinimumHeight(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def minimumSizeHint(self):
        return QSize(0, 0)

    def sizeHint(self):
        return QSize(240, 180)

    def set_columns(self, left: QWidget, right: QWidget):
        self._left = left
        self._right = right
        for w in (left, right):
            w.setParent(self)
            w.setMinimumWidth(0)
            w.setMaximumWidth(16777215)
            w.setMinimumHeight(0)
            w.setMaximumHeight(16777215)
            w.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
            w.show()
        self._place()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place()

    def _place(self):
        if self._left is None or self._right is None or self._placing:
            return
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return
        self._placing = True
        try:
            total = max(0, w - self._spacing)
            lw = int(round(total * self._left_frac)) if total else 0
            lw = max(0, min(total, lw))
            rw = total - lw
            # 与第二行输出卡右缘对齐（同属 _RatioCol 的 width），不再额外内缩
            self._left.setMinimumWidth(0)
            self._right.setMinimumWidth(0)
            self._left.setGeometry(0, 0, lw, h)
            self._right.setGeometry(lw + self._spacing, 0, rw, h)
        finally:
            self._placing = False


class _RatioCol(QWidget):
    """上下按固定比例切高。用 setGeometry 摆放，避免 setFixedHeight 把 min=max 锁死。"""

    def __init__(self, top_frac: float = 0.6, spacing: int = 8, parent=None):
        super().__init__(parent)
        self._top_frac = float(top_frac)
        self._spacing = int(spacing)
        self._top = None
        self._bottom = None
        self._placing = False
        self.setMinimumWidth(0)
        self.setMinimumHeight(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def minimumSizeHint(self):
        return QSize(0, 0)

    def sizeHint(self):
        return QSize(240, 280)

    def set_rows(self, top: QWidget, bottom: QWidget):
        self._top = top
        self._bottom = bottom
        for w in (top, bottom):
            w.setParent(self)
            w.setMinimumWidth(0)
            w.setMaximumWidth(16777215)
            w.setMinimumHeight(0)
            w.setMaximumHeight(16777215)
            w.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
            w.show()
        self._place()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place()

    def _place(self):
        if self._top is None or self._bottom is None or self._placing:
            return
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return
        self._placing = True
        try:
            total = max(0, h - self._spacing)
            th = int(round(total * self._top_frac)) if total else 0
            th = max(0, min(total, th))
            bh = total - th
            self._top.setGeometry(0, 0, w, th)
            self._bottom.setGeometry(0, th + self._spacing, w, bh)
        finally:
            self._placing = False


# ── 主页面 ─────────────────────────────────────────────────────────────────
class PageImageProc(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("PageRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAcceptDrops(True)

        self._src_path: str = ""
        self._save_path: str = ""
        self._work_img = None
        self._work_dirty = False
        self._prefs_dirty_cb = None
        self._theme_frames: List[QFrame] = []
        self._theme_titles: List[QLabel] = []
        self._out_cards: List[OutputImageCard] = []
        self._upscale_worker: Optional[_UpscaylWorker] = None
        self._upscale_temp = ""
        self._upscale_busy = False
        self._upscale_want_jpg = False
        self._op_cards = {}
        self._active_op = "scale"

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        left = self._build_left()
        right = self._build_right()
        top = _RatioRow(_LAYOUT_LEFT_FRAC, _LAYOUT_GAP)
        top.setObjectName("ImgProcTop")
        top.set_columns(left, right)

        bottom = self._build_output_preview()
        split = _RatioCol(_LAYOUT_TOP_FRAC, _LAYOUT_GAP)
        split.setObjectName("ImgProcSplit")
        split.set_rows(top, bottom)
        root.addWidget(split, 1)

        try:
            theme.changed.connect(self.refresh_theme)
        except Exception:
            pass

        for r in (
            self.radio_25, self.radio_50, self.radio_75,
            self.radio_save_jpg, self.radio_save_png,
        ):
            r.toggled.connect(self._on_option_toggled)
        for b in getattr(self, "_upscale_model_btns", []) or []:
            b.toggled.connect(self._on_option_toggled)

        self._update_actions_enabled()

    def _on_option_toggled(self, checked: bool):
        if checked:
            self._notify_prefs()

    # ── 布局：左 ────────────────────────────────────────────
    def _build_left(self) -> QWidget:
        wrap = QWidget()
        wrap.setObjectName("ImgProcLeft")
        wrap.setAttribute(Qt.WA_StyledBackground, True)
        wrap.setStyleSheet("#ImgProcLeft{background:transparent;}")
        wrap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay = QVBoxLayout(wrap)
        # 顶边 2px：与右列对齐，避免卡片上描边被父级裁掉
        lay.setContentsMargins(0, 2, 0, 0)
        lay.setSpacing(6)

        card = make_card("CardImgProcPreview")
        self._theme_frames.append(card)
        box = QVBoxLayout(card)
        box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        box.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        title_row.addStretch(1)
        self.btn_pick = apply_medium_button(QPushButton("选择图片"))
        self.btn_pick.clicked.connect(self._on_pick_file)
        title_row.addWidget(self.btn_pick, 0, Qt.AlignVCenter)
        box.addLayout(title_row)

        self.preview = _ImagePreview()
        self.preview.paths_dropped.connect(self._load_image)
        box.addWidget(self.preview, 1)

        self.lbl_meta = QLabel("拖拽图片到这里")
        self.lbl_meta.setWordWrap(True)
        self.lbl_meta.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self.lbl_meta.setStyleSheet(
            f"color:{tk('text_mut')};background:transparent;border:none;"
        )
        box.addWidget(self.lbl_meta)

        lay.addWidget(card, 1)
        return wrap

    # ── 布局：右 ────────────────────────────────────────────
    def _build_right(self) -> QWidget:
        wrap = QWidget()
        wrap.setObjectName("ImgProcRight")
        wrap.setAttribute(Qt.WA_StyledBackground, True)
        wrap.setStyleSheet("#ImgProcRight{background:transparent;}")
        wrap.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        wrap.setMinimumWidth(0)

        outer = QVBoxLayout(wrap)
        outer.setContentsMargins(0, 1, 0, 0)
        outer.setSpacing(6)
        try:
            outer.setSizeConstraint(QLayout.SetNoConstraint)
        except Exception:
            pass

        inner = QWidget()
        inner.setObjectName("ImgProcRightInner")
        inner.setAttribute(Qt.WA_StyledBackground, True)
        inner.setStyleSheet("#ImgProcRightInner{background:transparent;}")
        inner.setMinimumWidth(0)
        inner.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignTop)
        try:
            lay.setSizeConstraint(QLayout.SetMinimumSize)
        except Exception:
            pass

        def _pack_card(card: QFrame, box: QVBoxLayout) -> None:
            """卡片高度跟内容走，不能被压扁（否则放大六键会重叠）。"""
            card.setMinimumWidth(0)
            card.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
            try:
                box.setSizeConstraint(QLayout.SetMinimumSize)
            except Exception:
                pass
            box.setAlignment(Qt.AlignTop)

        # ① 图片缩小
        card_scale = make_card("CardImgProcScale")
        self._theme_frames.append(card_scale)
        vb = QVBoxLayout(card_scale)
        vb.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        vb.setSpacing(6)
        self._theme_titles.append(install_card_title(card_scale, vb, "图片缩小"))

        row_pct = QHBoxLayout()
        row_pct.setSpacing(12)
        self._scale_group = QButtonGroup(self)
        self.radio_25 = QRadioButton("25%")
        self.radio_50 = QRadioButton("50%")
        self.radio_75 = QRadioButton("75%")
        self.radio_50.setChecked(True)
        for i, r in enumerate((self.radio_25, self.radio_50, self.radio_75)):
            r.setStyleSheet("background:transparent;")
            self._scale_group.addButton(r, i)
            row_pct.addWidget(r)
        row_pct.addStretch(1)
        vb.addLayout(row_pct)
        _pack_card(card_scale, vb)
        lay.addWidget(card_scale, 0, Qt.AlignTop)

        # ② 图片放大 —— 6 个模型按钮六选一（不用下拉）
        card_up = make_card("CardImgProcUpscale")
        self._theme_frames.append(card_up)
        vb_up = QVBoxLayout(card_up)
        vb_up.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        vb_up.setSpacing(6)
        self._theme_titles.append(install_card_title(card_up, vb_up, "图片放大"))

        self._upscale_model_group = QButtonGroup(self)
        self._upscale_model_group.setExclusive(True)
        self._upscale_model_btns: List[QPushButton] = []
        grid_up = QGridLayout()
        grid_up.setContentsMargins(0, 0, 0, 0)
        grid_up.setHorizontalSpacing(6)
        grid_up.setVerticalSpacing(6)
        for i, (mid, title, _tip, _ftag) in enumerate(_UPSCAYL_MODELS):
            btn = QPushButton(title)
            btn.setObjectName("ImgProcUpscaleModel")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setProperty("modelId", mid)
            btn.setMinimumWidth(0)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self._upscale_model_group.addButton(btn, i)
            self._upscale_model_btns.append(btn)
            grid_up.addWidget(btn, i // 2, i % 2)
        for r in range(3):
            grid_up.setRowMinimumHeight(r, 26)
        if self._upscale_model_btns:
            self._upscale_model_btns[0].setChecked(True)
        vb_up.addLayout(grid_up)
        self._style_upscale_model_btns()

        self.slider_upscale = _ScaleSlider()
        self.slider_upscale.setValue(_UPSCAYL_SCALE_DEFAULT)
        self.slider_upscale.valueChanged.connect(self._on_upscale_scale_changed)
        vb_up.addWidget(self.slider_upscale)
        _pack_card(card_up, vb_up)
        lay.addWidget(card_up, 0, Qt.AlignTop)

        # ③ 四分切割
        card_split = make_card("CardImgProcSplit")
        self._theme_frames.append(card_split)
        vb2 = QVBoxLayout(card_split)
        vb2.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        vb2.setSpacing(6)
        self._theme_titles.append(install_card_title(card_split, vb2, "四分切割"))
        self.lbl_split_hint = QLabel("保存时均分成四块")
        self.lbl_split_hint.setStyleSheet(
            f"color:{tk('text_mut')};background:transparent;border:none;"
        )
        vb2.addWidget(self.lbl_split_hint)
        _pack_card(card_split, vb2)
        lay.addWidget(card_split, 0, Qt.AlignTop)

        # ④ 更多修改（镜像 / 旋转）
        card_more = make_card("CardImgProcMore")
        self._theme_frames.append(card_more)
        vb3 = QVBoxLayout(card_more)
        vb3.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        vb3.setSpacing(6)
        self._theme_titles.append(install_card_title(card_more, vb3, "更多修改"))

        self.btn_flip_h = QPushButton("水平")
        self.btn_flip_v = QPushButton("垂直")
        self.btn_rot_cw = QPushButton("顺90度")
        self.btn_rot_ccw = QPushButton("逆90度")
        self._geom_btns = [
            self.btn_flip_h, self.btn_flip_v, self.btn_rot_cw, self.btn_rot_ccw,
        ]
        for b in self._geom_btns:
            b.setObjectName("ImgProcGeomBtn")
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.setMinimumWidth(0)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_flip_h.clicked.connect(lambda: self._apply_geom("FLIP_LEFT_RIGHT", "水平镜像"))
        self.btn_flip_v.clicked.connect(lambda: self._apply_geom("FLIP_TOP_BOTTOM", "垂直镜像"))
        self.btn_rot_cw.clicked.connect(lambda: self._apply_geom("ROTATE_270", "顺时针旋转"))
        self.btn_rot_ccw.clicked.connect(lambda: self._apply_geom("ROTATE_90", "逆时针旋转"))

        grid_geom = QGridLayout()
        grid_geom.setContentsMargins(0, 0, 0, 0)
        grid_geom.setHorizontalSpacing(6)
        grid_geom.setVerticalSpacing(6)
        grid_geom.addWidget(self.btn_flip_h, 0, 0)
        grid_geom.addWidget(self.btn_flip_v, 0, 1)
        grid_geom.addWidget(self.btn_rot_cw, 1, 0)
        grid_geom.addWidget(self.btn_rot_ccw, 1, 1)
        for r in range(2):
            grid_geom.setRowMinimumHeight(r, 26)
        vb3.addLayout(grid_geom)
        self._style_geom_btns()

        _pack_card(card_more, vb3)
        lay.addWidget(card_more, 0, Qt.AlignTop)

        self._install_op_card("scale", card_scale)
        self._install_op_card("upscale", card_up)
        self._install_op_card("split", card_split)
        self._install_op_card("more", card_more)

        scroll = _QuietScroll()
        scroll.setObjectName("ImgProcRightScroll")
        try:
            scroll.viewport().setAutoFillBackground(False)
            scroll.viewport().setStyleSheet("background:transparent;")
        except Exception:
            pass
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        # 底栏：状态一行 + 格式/保存（钉在列底，不随四区滚动）
        foot = QWidget()
        foot.setObjectName("ImgProcSaveRow")
        foot.setAttribute(Qt.WA_StyledBackground, True)
        foot.setStyleSheet("#ImgProcSaveRow{background:transparent;border:none;}")
        foot.setMinimumWidth(0)
        foot.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Maximum)
        foot_v = QVBoxLayout(foot)
        foot_v.setContentsMargins(CARD_LEFT_GAP, 2, CARD_RIGHT_GAP, 2)
        foot_v.setSpacing(4)
        try:
            foot_v.setSizeConstraint(QLayout.SetNoConstraint)
        except Exception:
            pass

        self.lbl_op_status = QLabel("等待操作")
        self.lbl_op_status.setObjectName("ImgProcOpStatus")
        self.lbl_op_status.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.lbl_op_status.setFixedHeight(18)
        self.lbl_op_status.setWordWrap(False)
        self.lbl_op_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._style_op_status()
        foot_v.addWidget(self.lbl_op_status)

        foot_l = QHBoxLayout()
        foot_l.setContentsMargins(0, 0, 0, 0)
        foot_l.setSpacing(8)
        self._save_fmt_group = QButtonGroup(self)
        self.radio_save_jpg = QRadioButton("JPG")
        self.radio_save_png = QRadioButton("PNG")
        self.radio_save_jpg.setChecked(True)
        for i, r in enumerate((self.radio_save_jpg, self.radio_save_png)):
            r.setStyleSheet("background:transparent;")
            r.setMinimumWidth(0)
            self._save_fmt_group.addButton(r, i)
            foot_l.addWidget(r, 0, Qt.AlignVCenter)
        foot_l.addStretch(1)
        self.btn_save = _apply_save_button(QPushButton("保存"))
        self.btn_save.setMinimumWidth(0)
        self.btn_save.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.btn_save.clicked.connect(self._on_save_clicked)
        foot_l.addWidget(self.btn_save, 0, Qt.AlignVCenter)
        foot_v.addLayout(foot_l)
        outer.addWidget(foot, 0)
        return wrap

    # ── 布局：底 · 输出预览 ─────────────────────────────────
    def _build_output_preview(self) -> QWidget:
        card = make_card("CardImgProcOut")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._theme_frames.append(card)
        box = QVBoxLayout(card)
        box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        box.setSpacing(6)
        self._theme_titles.append(install_card_title(card, box, "输出预览"))

        # 纵向滚动：图片卡流式换行；高度由上下 60/40 比例区决定
        self.out_scroll = QScrollArea()
        self.out_scroll.setObjectName("ImgProcOutScroll")
        self.out_scroll.setWidgetResizable(True)
        self.out_scroll.setFrameShape(QFrame.NoFrame)
        self.out_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.out_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.out_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.out_scroll.setStyleSheet(
            "QScrollArea#ImgProcOutScroll{background:transparent;border:none;}"
            "QScrollArea#ImgProcOutScroll > QWidget > QWidget{background:transparent;}"
        )
        try:
            self.out_scroll.viewport().setAutoFillBackground(False)
            self.out_scroll.viewport().setStyleSheet("background:transparent;")
        except Exception:
            pass

        self._out_host = QWidget()
        self._out_host.setObjectName("ImgProcOutHost")
        self._out_host.setAttribute(Qt.WA_StyledBackground, True)
        self._out_host.setStyleSheet(
            "#ImgProcOutHost{background:transparent;border:none;}"
        )
        self._out_lay = FlowLayout(self._out_host, margin=0, h_spacing=10, v_spacing=10)

        self.out_scroll.setWidget(self._out_host)
        box.addWidget(self.out_scroll, 1)
        return card

    # ── 窗口高度保护 ────────────────────────────────────────
    def hasHeightForWidth(self):
        return False

    def minimumSizeHint(self):
        return QSize(0, 0)

    def sizeHint(self):
        return QSize(720, 560)

    def _outline_btn_qss(self, obj_name: str, with_checked: bool = False) -> str:
        """描边胶囊：与「图片放大」六键同一套。"""
        try:
            bd = tk("border")
            bg = "transparent"
            fg = tk("text")
            mut = tk("text_mut")
            on_bd = tk("accent")
            on_bg = tk("sel_bg")
            on_fg = tk("sel_text")
        except Exception:
            bd, bg, fg = "#25345c", "transparent", "#d7def7"
            mut = "#9fb0d7"
            on_bd, on_bg, on_fg = "#3a8ee0", "#1f3a8a", "#93c5fd"
        qss = (
            f"QPushButton#{obj_name}{{"
            f"background:{bg};color:{fg};border:1px solid {bd};"
            f"border-radius:6px;padding:2px 4px;font-size:12px;font-weight:600;"
            f"min-height:24px;max-height:26px;}}"
            f"QPushButton#{obj_name}:hover{{"
            f"border-color:{on_bd};color:{on_fg};}}"
            f"QPushButton#{obj_name}:disabled{{"
            f"color:{mut};border-color:{bd};}}"
        )
        if with_checked:
            qss += (
                f"QPushButton#{obj_name}:checked{{"
                f"background:{on_bg};border-color:{on_bd};color:{on_fg};}}"
            )
        return qss

    def _style_upscale_model_btns(self):
        qss = self._outline_btn_qss("ImgProcUpscaleModel", with_checked=True)
        for b in getattr(self, "_upscale_model_btns", []) or []:
            b.setStyleSheet(qss)

    def _style_geom_btns(self):
        qss = self._outline_btn_qss("ImgProcGeomBtn", with_checked=False)
        for b in getattr(self, "_geom_btns", []) or []:
            b.setStyleSheet(qss)

    def _style_upscale_scale_slider(self):
        sl = getattr(self, "slider_upscale", None)
        if sl is not None:
            sl.update()

    def _upscale_scale(self) -> int:
        sl = getattr(self, "slider_upscale", None)
        try:
            n = int(sl.value()) if sl is not None else _UPSCAYL_SCALE_DEFAULT
        except (TypeError, ValueError):
            n = _UPSCAYL_SCALE_DEFAULT
        return max(_UPSCAYL_SCALE_MIN, min(_UPSCAYL_SCALE_MAX, n))

    def _on_upscale_scale_changed(self, value: int):
        try:
            n = max(_UPSCAYL_SCALE_MIN, min(_UPSCAYL_SCALE_MAX, int(value)))
        except (TypeError, ValueError):
            n = _UPSCAYL_SCALE_DEFAULT
        self._notify_prefs()

    def _upscale_model_id(self) -> str:
        for b in getattr(self, "_upscale_model_btns", []) or []:
            if b.isChecked():
                mid = str(b.property("modelId") or "").strip()
                if mid in _UPSCAYL_IDS:
                    return mid
        return _UPSCAYL_DEFAULT

    # ── 主题 ────────────────────────────────────────────────
    def refresh_theme(self, *_):
        for fr in self._theme_frames:
            try:
                restyle_card_frame(fr)
            except Exception:
                pass
        for t in self._theme_titles:
            try:
                restyle_card_title(t)
            except Exception:
                pass
        try:
            self.preview.refresh_theme()
        except Exception:
            pass
        try:
            self.lbl_meta.setStyleSheet(
                f"color:{tk('text_mut')};background:transparent;border:none;"
            )
        except Exception:
            pass
        try:
            self._style_op_status()
        except Exception:
            pass
        try:
            if getattr(self, "lbl_split_hint", None) is not None:
                self.lbl_split_hint.setStyleSheet(
                    f"color:{tk('text_mut')};background:transparent;border:none;"
                )
        except Exception:
            pass
        for c in self._out_cards:
            try:
                c.refresh_theme()
            except Exception:
                pass
        self._style_upscale_model_btns()
        self._style_geom_btns()
        self._style_upscale_scale_slider()
        for card in (getattr(self, "_op_cards", None) or {}).values():
            glass = getattr(card, "_op_glass", None)
            if glass is not None:
                try:
                    glass.update()
                except Exception:
                    pass
        self._update_actions_enabled()

    # ── 偏好 ────────────────────────────────────────────────
    def set_prefs_dirty_callback(self, cb):
        self._prefs_dirty_cb = cb

    def _notify_prefs(self):
        if callable(self._prefs_dirty_cb):
            try:
                self._prefs_dirty_cb()
            except Exception:
                pass

    def export_settings(self) -> dict:
        pct = 50
        if self.radio_25.isChecked():
            pct = 25
        elif self.radio_75.isChecked():
            pct = 75
        fmt = "jpg" if self._save_is_jpg() else "png"
        return {
            "save_path": (self._save_path or "").replace("\\", "/"),
            "scale_pct": pct,
            "save_format": fmt,
            "split_format": fmt,
            "convert_format": fmt,
            "upscale_model": self._upscale_model_id(),
            "upscale_scale": self._upscale_scale(),
            "active_op": self._active_op if self._active_op in _OP_KEYS else "scale",
        }

    def apply_settings(self, d: dict):
        if not isinstance(d, dict):
            return
        sp = (d.get("save_path") or "").strip()
        if sp:
            self._save_path = sp.replace("\\", "/")

        try:
            pct = int(d.get("scale_pct", 50) or 50)
        except (TypeError, ValueError):
            pct = 50
        if pct <= 25:
            self.radio_25.setChecked(True)
        elif pct >= 75:
            self.radio_75.setChecked(True)
        else:
            self.radio_50.setChecked(True)

        raw_fmt = d.get("save_format") or d.get("convert_format") or d.get("split_format") or "jpg"
        fmt = str(raw_fmt).lower()
        if fmt in ("png",):
            self.radio_save_png.setChecked(True)
        else:
            self.radio_save_jpg.setChecked(True)

        mid = str(d.get("upscale_model") or _UPSCAYL_DEFAULT).strip()
        if mid not in _UPSCAYL_IDS:
            mid = _UPSCAYL_DEFAULT
        for b in getattr(self, "_upscale_model_btns", []) or []:
            if str(b.property("modelId") or "") == mid:
                b.setChecked(True)
                break

        try:
            xs = int(d.get("upscale_scale", _UPSCAYL_SCALE_DEFAULT) or _UPSCAYL_SCALE_DEFAULT)
        except (TypeError, ValueError):
            xs = _UPSCAYL_SCALE_DEFAULT
        xs = max(_UPSCAYL_SCALE_MIN, min(_UPSCAYL_SCALE_MAX, xs))
        sl = getattr(self, "slider_upscale", None)
        if sl is not None:
            sl.blockSignals(True)
            sl.setValue(xs)
            sl.blockSignals(False)
            self._on_upscale_scale_changed(xs)

        op = str(d.get("active_op") or "scale").strip()
        if op not in _OP_KEYS:
            op = "scale"
        self._active_op = op
        self._update_actions_enabled()

    # ── 输入 ────────────────────────────────────────────────
    def _on_pick_file(self):
        start = ""
        if self._src_path and os.path.isfile(self._src_path):
            start = os.path.dirname(self._src_path)
        elif self._save_path:
            start = self._save_path
        path, _ = QFileDialog.getOpenFileName(self, "选择图片", start, _IMG_FILTER)
        if path:
            self._load_image(path)

    def _load_image(self, path: str):
        path = (path or "").strip()
        if not path or not os.path.isfile(path):
            self._status("无效路径", ok=False, hint="无效路径")
            return
        ext = os.path.splitext(path)[1].lower()
        if ext not in _IMG_EXTS:
            self._status(f"不支持的格式：{ext or '无扩展名'}", ok=False, hint="格式不支持")
            return
        if not self.preview.set_image_path(path):
            self._status(f"无法预览：{os.path.basename(path)}", ok=False, hint="无法预览")
            return
        self._discard_work()
        self._src_path = path
        self._refresh_meta()
        self._update_actions_enabled()
        self._set_op_status("已载入图片")

    def _refresh_meta(self):
        p = self._src_path
        if not p:
            self.lbl_meta.setText("拖拽图片到这里")
            return
        name = os.path.basename(p)
        w = h = 0
        size = 0
        if self._work_img is not None:
            w, h = self._work_img.size
        if os.path.isfile(p):
            fw, fh, size = _read_image_meta(p)
            if w <= 0 or h <= 0:
                w, h = fw, fh
        elif w <= 0:
            self.lbl_meta.setText("拖拽图片到这里")
            return
        res = f"{w}×{h}" if w and h else "—×—"
        size_s = _fmt_size(size) if size else "—"
        self.lbl_meta.setText(f"{name}  ·  {res}  ·  {size_s}")

    def _install_op_card(self, key: str, card: QFrame) -> None:
        glass = _OpGlass(card)
        glass.clicked.connect(lambda _=False, k=key: self._set_active_op(k))
        card._op_glass = glass
        self._op_cards[key] = card
        glass.hide()

    def _set_active_op(self, key: str) -> None:
        if key not in _OP_KEYS:
            return
        if getattr(self, "_active_op", "") == key:
            return
        self._active_op = key
        self._update_actions_enabled()
        self._notify_prefs()
        self._set_op_status("已选%s" % _OP_STATUS_NAMES.get(key, "功能"))

    def _update_actions_enabled(self):
        ok = bool(self._src_path and os.path.isfile(self._src_path))
        busy = bool(self._upscale_busy)
        active = getattr(self, "_active_op", "") or ""
        cards = getattr(self, "_op_cards", None) or {}

        for key, card in cards.items():
            on = key == active
            glass = getattr(card, "_op_glass", None)
            if glass is not None:
                glass.setVisible(not on)
                if not on:
                    glass.raise_()

        def _en(w, want):
            if w is None:
                return
            try:
                w.setEnabled(bool(want))
            except Exception:
                pass

        _en(getattr(self, "btn_save", None), bool(active) and ok and not busy)
        for r in (getattr(self, "radio_save_jpg", None), getattr(self, "radio_save_png", None)):
            _en(r, not busy)
        for b in (
            getattr(self, "btn_flip_h", None), getattr(self, "btn_flip_v", None),
            getattr(self, "btn_rot_cw", None), getattr(self, "btn_rot_ccw", None),
        ):
            _en(b, active == "more" and ok and not busy)
        for b in getattr(self, "_upscale_model_btns", []) or []:
            _en(b, active == "upscale" and not busy)
        _en(getattr(self, "slider_upscale", None), active == "upscale" and not busy)
        for r in (
            getattr(self, "radio_25", None), getattr(self, "radio_50", None),
            getattr(self, "radio_75", None),
        ):
            _en(r, active == "scale")

    # ── 拖放 / 粘贴 ─────────────────────────────────────────
    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData() and e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e: QDropEvent):
        md = e.mimeData()
        if not md or not md.hasUrls():
            e.ignore()
            return
        for url in md.urls():
            p = url.toLocalFile()
            if p and os.path.isfile(p) and os.path.splitext(p)[1].lower() in _IMG_EXTS:
                self._load_image(p)
                e.acceptProposedAction()
                return
        e.ignore()

    def keyPressEvent(self, event):
        if event.modifiers() & Qt.ControlModifier and event.key() == Qt.Key_V:
            if self._try_paste_clipboard():
                event.accept()
                return
        super().keyPressEvent(event)

    def _try_paste_clipboard(self) -> bool:
        cb = QApplication.clipboard()
        if cb is None:
            return False
        md = cb.mimeData()
        if md is None:
            return False
        if md.hasUrls():
            for url in md.urls():
                p = url.toLocalFile()
                if p and os.path.isfile(p) and os.path.splitext(p)[1].lower() in _IMG_EXTS:
                    self._load_image(p)
                    return True
        if md.hasImage():
            qimg = cb.image()
            if qimg is not None and not qimg.isNull():
                return self._load_from_qimage(qimg)
        return False

    def _load_from_qimage(self, qimg: QImage) -> bool:
        out_dir = self._ensure_out_dir()
        if not out_dir:
            return False
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = _unique_path(out_dir, f"clipboard_{ts}", ".png")
        pm = QPixmap.fromImage(qimg)
        if pm.isNull() or not pm.save(path, "PNG"):
            self._status("剪贴板图片保存失败", ok=False, hint="剪贴失败")
            return False
        self._load_image(path)
        return True

    # ── 输出目录 ────────────────────────────────────────────
    def _ensure_out_dir(self) -> str:
        d = (self._save_path or "").strip()
        if not d:
            d = os.path.join(os.path.expanduser("~/Downloads"), "ImageProc")
            self._save_path = d.replace("\\", "/")
        try:
            ensure_dir(d)
            return d
        except Exception as e:
            self._status(f"输出目录不可用：{e}", ok=False, hint="目录不可用")
            return ""

    def _open_path_dir(self, path: str):
        """单击图片卡：打开该文件所在目录。"""
        if not path:
            return
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        if folder and os.path.isdir(folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(folder)))

    # ── 输出预览卡 ──────────────────────────────────────────
    def _add_output_cards(self, paths: List[str]):
        """追加输出卡（流式换行），并滚到最底。"""
        added = False
        for path in paths or []:
            if not path or not os.path.isfile(path):
                continue
            card = OutputImageCard(path)
            card.clicked_open.connect(self._open_path_dir)
            self._out_lay.addWidget(card)
            self._out_cards.append(card)
            added = True
            log.info("输出：%s", path)

        if added:
            self._relayout_out_cards()
            QTimer.singleShot(0, self._scroll_out_to_end)
            QTimer.singleShot(50, self._scroll_out_to_end)

    def _relayout_out_cards(self):
        """FlowLayout 需按 width 算高，否则滚动区内容高度不够。"""
        sc = getattr(self, "out_scroll", None)
        lay = getattr(self, "_out_lay", None)
        if sc is None or lay is None:
            return
        inner = sc.widget()
        if inner is None:
            return
        try:
            lay.invalidate()
        except Exception:
            pass
        for _ in range(2):
            w = max(1, sc.viewport().width() or sc.width() or inner.width() or 1)
            need_h = max(0, int(lay.heightForWidth(w)))
            if inner.minimumHeight() != need_h:
                inner.setMinimumHeight(need_h)
            inner.updateGeometry()

    def _scroll_out_to_end(self):
        try:
            self._relayout_out_cards()
            bar = self.out_scroll.verticalScrollBar()
            if bar is not None:
                bar.setValue(bar.maximum())
        except Exception:
            pass

    def _style_op_status(self) -> None:
        lbl = getattr(self, "lbl_op_status", None)
        if lbl is None:
            return
        try:
            color = tk("accent")
        except Exception:
            color = "#3a8ee0"
        lbl.setStyleSheet(
            f"color:{color};background:transparent;border:none;"
            f"font-size:12px;font-weight:600;"
        )

    def _set_op_status(self, text: str) -> None:
        """保存按钮上一行：最多 10 字，单行显示。"""
        s = (text or "").replace("\n", "").strip()
        if len(s) > _OP_STATUS_MAX:
            s = s[:_OP_STATUS_MAX]
        lbl = getattr(self, "lbl_op_status", None)
        if lbl is not None:
            lbl.setText(s)

    def _status(self, msg: str, ok: bool = True, hint: str = ""):
        """写日志，并在底栏状态行给出不超过 10 字的短提示。"""
        if ok:
            log.info("%s", msg)
        else:
            log.warning("%s", msg)
        self._set_op_status((hint or "").strip() or (msg or ""))

    def _save_is_jpg(self) -> bool:
        r = getattr(self, "radio_save_jpg", None)
        if r is None:
            return True
        return bool(r.isChecked())

    def _save_ext(self) -> str:
        return ".jpg" if self._save_is_jpg() else ".png"

    def _on_save_clicked(self):
        op = getattr(self, "_active_op", "") or "scale"
        if op == "scale":
            self._do_scale()
        elif op == "upscale":
            self._do_upscale()
        elif op == "split":
            self._do_split()
        else:
            self._do_save()

    # ── 业务：缩小 ──────────────────────────────────────────
    def _scale_pct(self) -> int:
        if self.radio_25.isChecked():
            return 25
        if self.radio_75.isChecked():
            return 75
        return 50

    def _do_scale(self):
        if not self._require_src():
            return
        if not _has_pillow():
            self._status("未安装 Pillow，请 pip install Pillow", ok=False, hint="缺少组件")
            return
        out_dir = self._ensure_out_dir()
        if not out_dir:
            return
        pct = self._scale_pct()
        self._notify_prefs()
        self._set_op_status("正在缩小")
        try:
            from PIL import Image as PILImage
            try:
                resample = PILImage.Resampling.LANCZOS
            except AttributeError:
                resample = PILImage.LANCZOS
            img = self._open_current()
            w, h = img.size
            nw = max(1, int(w * pct / 100))
            nh = max(1, int(h * pct / 100))
            resized = img.resize((nw, nh), resample)
            stem = _src_stem(self._src_path)
            ext = self._save_ext()
            out = _op_save_path(out_dir, stem, "缩小%s" % pct, ext)
            if ext == ".jpg":
                _save_jpg(resized, out)
            else:
                _save_png(resized, out)
            if img is not self._work_img:
                img.close()
            try:
                resized.close()
            except Exception:
                pass
            self._discard_work()
            self._add_output_cards([out])
            self.preview.set_image_path(out)
            self._refresh_meta()
            self._set_op_status("缩小完成")
        except Exception as e:
            log.exception("图片缩小失败")
            self._status(f"缩小失败：{e}", ok=False, hint="缩小失败")

    # ── 业务：放大（Upscayl / Real-ESRGAN，2×–6×）────────────
    def _do_upscale(self):
        if self._upscale_busy:
            return
        if not self._require_src():
            return
        exe, models = _upscayl_exe_and_models()
        if not exe or not os.path.isfile(exe):
            self._status("未找到 upscayl-bin.exe（请放在 model/upscayl）", ok=False, hint="缺少程序")
            return
        mid = self._upscale_model_id()
        if not _upscayl_model_ok(models, mid):
            self._status("缺少模型文件：%s.bin / .param" % mid, ok=False, hint="缺少模型")
            return
        out_dir = self._ensure_out_dir()
        if not out_dir:
            return
        self._notify_prefs()
        src, is_temp = self._prepare_upscale_input(out_dir)
        if not src:
            return
        stem = _src_stem(self._src_path)
        x = self._upscale_scale()
        tag = "%s%sx" % (_UPSCAYL_FILE_TAG.get(mid, "放大清晰"), x)
        dst = _op_save_path(out_dir, stem, tag, ".png")
        self._upscale_temp = src if is_temp else ""
        w = _UpscaylWorker(exe, models, mid, src, dst, scale=x)
        w.progress.connect(self._on_upscale_progress)
        w.finished_ok.connect(self._on_upscale_ok)
        w.finished_err.connect(self._on_upscale_err)
        w.finished.connect(self._on_upscale_thread_done)
        self._upscale_worker = w
        self._upscale_busy = True
        self._upscale_want_jpg = self._save_is_jpg()
        self._update_actions_enabled()
        self._set_op_status("正在放大")
        self._status("开始放大 %s×（%s）" % (x, tag), hint="正在放大")
        w.start()

    def _prepare_upscale_input(self, out_dir: str) -> Tuple[str, bool]:
        """upscayl-bin 只吃 png/jpg/webp；有未保存修改或其它格式时先落一张临时 PNG。"""
        src = self._src_path
        ext = os.path.splitext(src)[1].lower()
        need_tmp = bool(self._work_img is not None and self._work_dirty)
        if not need_tmp and ext in _UPSCAYL_DIRECT_EXTS and os.path.isfile(src):
            return src, False
        if not _has_pillow():
            self._status("未安装 Pillow，无法转换后放大", ok=False, hint="缺少组件")
            return "", False
        try:
            img = self._open_current()
            tmp = _unique_path(out_dir, "_upscale_in", ".png")
            _save_png(img, tmp)
            if img is not self._work_img:
                try:
                    img.close()
                except Exception:
                    pass
            return tmp, True
        except Exception as e:
            log.exception("放大前转 PNG 失败")
            self._status("放大前转换失败：%s" % e, ok=False, hint="转换失败")
            return "", False

    def _on_upscale_progress(self, pct: int):
        try:
            n = max(0, min(100, int(pct)))
            self._set_op_status("放大中%s%%" % n)
        except Exception:
            pass

    def _reset_upscale_btn_text(self):
        btn = getattr(self, "btn_save", None)
        if btn is None:
            return
        try:
            btn.setText("保存")
        except Exception:
            pass

    def _finalize_upscale_path(self, path: str) -> str:
        """upscayl 固定出 PNG；用户选 JPG 时再转一层。"""
        if not path or not os.path.isfile(path):
            return path
        if not getattr(self, "_upscale_want_jpg", False):
            return path
        ext = os.path.splitext(path)[1].lower()
        if ext in (".jpg", ".jpeg"):
            return path
        try:
            img = _open_pil(path)
            dst = os.path.splitext(path)[0] + ".jpg"
            if os.path.exists(dst):
                stem = os.path.splitext(os.path.basename(path))[0]
                dst = _unique_path(os.path.dirname(path), stem, ".jpg")
            _save_jpg(img, dst)
            try:
                img.close()
            except Exception:
                pass
            try:
                os.remove(path)
            except Exception:
                log.debug("删除放大 PNG 失败 path=%s", path, exc_info=True)
            return dst
        except Exception:
            log.exception("放大结果转 JPG 失败")
            return path

    def _on_upscale_ok(self, path: str):
        self._cleanup_upscale_temp()
        self._upscale_busy = False
        self._reset_upscale_btn_text()
        path = self._finalize_upscale_path(path)
        self._discard_work()
        self._add_output_cards([path])
        try:
            self.preview.set_image_path(path)
        except Exception:
            pass
        self._refresh_meta()
        self._update_actions_enabled()
        self._status("放大完成：%s" % os.path.basename(path), hint="放大完成")

    def _on_upscale_err(self, msg: str):
        self._cleanup_upscale_temp()
        self._upscale_busy = False
        self._reset_upscale_btn_text()
        self._update_actions_enabled()
        self._status(msg or "放大失败", ok=False, hint="放大失败")

    def _on_upscale_thread_done(self):
        w = self._upscale_worker
        self._upscale_worker = None
        if w is not None:
            try:
                w.deleteLater()
            except Exception:
                pass
        if self._upscale_busy:
            self._upscale_busy = False
            self._reset_upscale_btn_text()
            self._update_actions_enabled()

    def _cleanup_upscale_temp(self):
        p = self._upscale_temp
        self._upscale_temp = ""
        if p and os.path.isfile(p):
            try:
                os.remove(p)
            except Exception:
                log.debug("删除放大临时文件失败 path=%s", p, exc_info=True)

    def shutdown(self):
        """主窗关闭：停掉放大子进程，避免 QThread 残留。"""
        w = self._upscale_worker
        self._upscale_worker = None
        self._upscale_busy = False
        self._reset_upscale_btn_text()
        if w is not None:
            try:
                w.stop()
            except Exception:
                pass
            try:
                from utils.qthread_util import stop_qthread
                stop_qthread(w, name="upscayl")
            except Exception:
                log.exception("停止放大线程失败")
        self._cleanup_upscale_temp()

    # ── 业务：四分切割 ──────────────────────────────────────
    def _do_split(self):
        if not self._require_src():
            return
        if not _has_pillow():
            self._status("未安装 Pillow，请 pip install Pillow", ok=False, hint="缺少组件")
            return
        out_dir = self._ensure_out_dir()
        if not out_dir:
            return
        use_jpg = self._save_is_jpg()
        ext = ".jpg" if use_jpg else ".png"
        self._notify_prefs()
        self._set_op_status("正在切割")
        try:
            img = self._open_current()
            w, h = img.size
            mid_x, mid_y = w // 2, h // 2
            quads = (
                (_SPLIT_FILE_TAGS[0], (0, 0, mid_x, mid_y)),
                (_SPLIT_FILE_TAGS[1], (mid_x, 0, w, mid_y)),
                (_SPLIT_FILE_TAGS[2], (0, mid_y, mid_x, h)),
                (_SPLIT_FILE_TAGS[3], (mid_x, mid_y, w, h)),
            )
            stem = _src_stem(self._src_path)
            date = datetime.now().strftime("%Y%m%d")
            ver = _max_op_version(out_dir, stem, date, _SPLIT_FILE_TAGS, ext) + 1
            outs = []
            for tag, box in quads:
                crop = img.crop(box)
                out = _op_save_path(out_dir, stem, tag, ext, ver=ver)
                if use_jpg:
                    _save_jpg(crop, out)
                else:
                    _save_png(crop, out)
                try:
                    crop.close()
                except Exception:
                    pass
                outs.append(out)
            if img is not self._work_img:
                img.close()
            self._discard_work()
            self._add_output_cards(outs)
            if outs:
                self.preview.set_image_path(outs[0])
            self._refresh_meta()
            self._set_op_status("切割完成")
        except Exception as e:
            log.exception("四分切割失败")
            self._status(f"切割失败：{e}", ok=False, hint="切割失败")

    # ── 业务：更多修改（镜像 / 旋转 / 保存） ────────────────
    def _discard_work(self) -> None:
        img = self._work_img
        self._work_img = None
        self._work_dirty = False
        if img is None:
            return
        try:
            img.close()
        except Exception:
            log.debug("关闭工作图失败", exc_info=True)

    def _ensure_work(self):
        if self._work_img is None:
            self._work_img = _open_pil(self._src_path)
            self._work_dirty = False
        return self._work_img

    def _open_current(self):
        if self._work_img is not None:
            return self._work_img.copy()
        return _open_pil(self._src_path)

    def _show_work_preview(self) -> bool:
        img = self._work_img
        if img is None:
            return False
        pm = _pil_to_qpixmap(img)
        if not self.preview.set_pixmap(pm):
            self._status("预览刷新失败", ok=False, hint="预览失败")
            return False
        return True

    def _apply_geom(self, op: str, label: str) -> None:
        if not self._require_src():
            return
        if not _has_pillow():
            self._status("未安装 Pillow，请 pip install Pillow", ok=False, hint="缺少组件")
            return
        try:
            flag = _pil_transpose_flag(op)
            img = self._ensure_work()
            nxt = img.transpose(flag)
            if nxt is not img:
                try:
                    img.close()
                except Exception:
                    log.debug("关闭上一张工作图失败", exc_info=True)
            self._work_img = nxt
            self._work_dirty = True
            self._show_work_preview()
            self._refresh_meta()
            done = {
                "水平镜像": "已水平镜像",
                "垂直镜像": "已垂直镜像",
                "顺时针旋转": "已顺时针转",
                "逆时针旋转": "已逆时针转",
            }.get(label, "修改完成")
            self._set_op_status(done)
        except Exception as e:
            log.exception("%s失败", label)
            fail = "旋转失败" if "旋转" in (label or "") else "镜像失败"
            self._status(f"{label}失败：{e}", ok=False, hint=fail)

    def _do_save(self):
        if not self._require_src():
            return
        if not _has_pillow():
            self._status("未安装 Pillow，请 pip install Pillow", ok=False, hint="缺少组件")
            return
        out_dir = self._ensure_out_dir()
        if not out_dir:
            return
        to_jpg = self._save_is_jpg()
        ext = ".jpg" if to_jpg else ".png"
        self._notify_prefs()
        self._set_op_status("正在保存")
        owned = False
        img = self._work_img
        if img is None:
            img = _open_pil(self._src_path)
            owned = True
        try:
            stem = os.path.splitext(os.path.basename(self._src_path))[0]
            out = _unique_path(out_dir, stem, ext)
            if to_jpg:
                _save_jpg(img, out)
            else:
                _save_png(img, out)
            self._discard_work()
            self._src_path = out
            self._add_output_cards([out])
            self.preview.set_image_path(out)
            self._refresh_meta()
            self._update_actions_enabled()
            self._set_op_status("保存完成")
        except Exception as e:
            log.exception("保存失败")
            self._status(f"保存失败：{e}", ok=False, hint="保存失败")
        finally:
            if owned and img is not None:
                try:
                    img.close()
                except Exception:
                    log.debug("关闭保存用临时图失败", exc_info=True)

    def _require_src(self) -> bool:
        if not self._src_path or not os.path.isfile(self._src_path):
            self._status("请先选择或拖入一张图片", ok=False, hint="尚未选图")
            return False
        return True
