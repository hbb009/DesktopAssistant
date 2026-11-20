# pages/page_pixel_board.py
# 像素画板：画板、动效两个分页，下面一块调色盘（含两边共用的工具栏）。
# 画板：当前方向一张大画布 + 四方向缩略图条，每个方向自己分层。
# 动效以一张底图做出一条连续帧：时间轴最多 64 帧，洋葱皮、每帧时长、循环/乒乓播放。
# 格子 16/24/32 或直接在工具条填 宽×高（8–512，可不等）；调色盘 64 色 + 16 个自定义色块，可全屏吸管取色。
# 画布：滚轮缩放、中键或空格+左键平移、网格、悬停坐标；每个绘制目标各有撤销栈。

from __future__ import annotations

import os

from PyQt5.QtCore import QEvent, QMimeData, QObject, Qt, QPoint, QPointF, QRect, QRectF, QSize, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QDrag, QFont, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPolygonF
from PyQt5.QtWidgets import (
    QAbstractSpinBox, QSpinBox, QScrollArea, QApplication, QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QShortcut,
    QSizePolicy, QSlider, QTabWidget, QVBoxLayout, QWidget, QFrame, QGridLayout, QInputDialog, QMenu,
    QComboBox, QDialog,
)
import zlib
import hashlib

from PIL import Image

from styles.file_drop import file_drop_pixmap, paint_file_drop_outline
from styles.style_all import TAB_QSS, VIDEO_TAB_QSS, apply_std_button, build_std_button_qss, fmt, theme, tk
from utils.logger import get_logger
from utils.pixel_project import (
    PXA_EXT, PXB_EXT, WRONG_KIND, ProjectError, load_art_project, load_project, save_art_project, save_project,
)
from utils.pixel_walk import (
    CELL_MAX, CELL_MIN, cell_wh, palette_64, CELLS, DIR_LABEL, DIRS, PALETTES, blank_cell, clean_stem, compose_clip_sheet,
    compose_sheet, composite_layers, flood_fill, frame_has_pixels, has_transparency,
    hex_rgb, load_image, next_png_path, prepare_cell, quantize_rgba, resize_cell,
    fit_note, frame_count, recanvas, next_free_path,
    snap_cell, snap_colors, write_gif,
    ALIGNS, ALIGN_DEFAULT, norm_align, group_paths, natural_key, place_image,
    ellipse_points, line_points, rect_points, shift_image, tint_ghost, write_gif_timed,
)

# 本页所有按钮统一：内边距 1px 4px（原选择按钮 2px 8px、全局按钮 4px 14px 的一半档），高 22（原 24~29）
_BTN_H = 22
_BTN_PAD = "1px 4px"
_BTN_QSS = f"padding:{_BTN_PAD};font-size:12px;"
_CUSTOM_SLOTS = 16
# 第九阶段：图标按钮是 _unify_buttons 的唯一例外，尺寸统一写在这里（tool=调色区工具/选项，small=图层行，corner=缩略图角标）
_ICON_SIZES = {"tool": 28, "small": 22, "corner": 20}
_ICON_BTN = _ICON_SIZES["tool"]
_ART_SPEC = "点缩略图或按 1/2/3/4 切 正/左/后/右；PageUp/PageDown 切图层；Ctrl+S 存当前 PNG；K 全屏吸管；格子可填宽×高（8–512）；工程 .pxb 可直接拖进来"
_WALK_SPEC = ("动效：一条连续帧，底图复制到帧上再改；最多 64 帧，可左移右移、设每帧时长；洋葱皮只显示不导出；"
              "Ctrl+S 存当前帧 PNG；工程 .pxa 可直接拖进来")
_PXA_FILTER = "像素动效工程 (*.pxa)"
_PXB_FILTER = "像素画板工程 (*.pxb)"
_TIMELINE_TIP = ("时间轴：点选帧；拖动缩略图可重排；右键或悬停出现 复制/删除\n"
                 "←/→ 切帧（动效页、焦点不在输入框）；Alt+←/→ 移动当前帧\n"
                 "焦点在时间轴上时 +/- 调当前帧时长（不缩放画布）；Space 播放/停止\n"
                 "底图格（左侧）可拖入图片，存底图在右上角；最多 64 帧，可拖入图片替换某帧（一次拖一个，多张用「批量导入」）")
_FRAME_MIME = "application/x-pixelboard-frame"
_DIR_MIME = "application/x-pixelboard-art-dir"   # 第十五阶段：画板页四向缩略图互换
_SIDE_W = {False: 260, True: 400}   # 预览栏宽度：普通 / 放大
_CELL_PRESETS = (16, 24, 32)   # 画板/动效格子预设；其余尺寸在宽×高框里填
_COLOR_ITEMS = ((0, "原色"), (16, "16色"), (32, "32色"))
_PRESET_SWATCHES = 64
_TOOLS = (
    ("pencil", "铅笔"), ("eraser", "橡皮"), ("fill", "填充"),
    ("line", "直线"), ("rect", "矩形"), ("ellipse", "椭圆"), ("select", "选区"),
)
_TOOL_KEYS = {"pencil": "B", "eraser": "E", "fill": "G",
              "line": "L", "rect": "R", "ellipse": "O", "select": "M"}
_SHAPES = ("line", "rect", "ellipse")
_HISTORY_MAX = 50
_HISTORY_MAX_BIG = 20            # 大于 256×256 的图每个目标最多留 20 步
_HISTORY_BYTES = 256 * 1024 * 1024   # 所有撤销/重做记录合计上限，超出丢最老的
_MAX_LAYERS = 6
_MAX_FRAMES = 64   # 第十一阶段由 24 提到 64
_HOLDS = (1, 2, 3, 4)


log = get_logger(__name__)

_IMAGE_EXTS = {".png", ".webp", ".bmp", ".gif", ".jpg", ".jpeg"}
_OPEN_FILTER = "图片 (*.png *.webp *.bmp *.gif *.jpg *.jpeg)"


def _image_paths(mime) -> list:
    if mime is None or not mime.hasUrls():
        return []
    out = []
    for url in mime.urls():
        path = url.toLocalFile()
        if not path or not os.path.isfile(path):
            continue
        if os.path.splitext(path)[1].lower() in _IMAGE_EXTS:
            out.append(path)
    return out


def _pxa_path(mime):
    """拖进来的恰好是一个工程文件（.pxa 动效 / .pxb 画板）时返回路径，否则 None；种类由接收页判断并提示。"""
    if mime is None or not mime.hasUrls() or len(mime.urls()) != 1:
        return None
    path = mime.urls()[0].toLocalFile()
    if path and os.path.isfile(path) and os.path.splitext(path)[1].lower() in (PXA_EXT, PXB_EXT):
        return path
    return None


def _accept_image_drag(event) -> bool:
    paths = _image_paths(event.mimeData())
    if not paths:
        event.ignore()
        return False
    event.acceptProposedAction()
    return True


def _pil_to_qimage(im) -> QImage:
    rgba = im.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    w, h = rgba.size
    qimg = QImage(data, w, h, w * 4, QImage.Format_RGBA8888)
    return qimg.copy()


def _paint_checker(p: QPainter, rect, step: int = 8) -> None:
    c0 = QColor("#FFFFFF")
    c1 = QColor("#C0C0C0")
    x0, y0, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    y = 0
    row = 0
    while y < h:
        x = 0
        col = 0
        while x < w:
            p.fillRect(x0 + x, y0 + y, min(step, w - x), min(step, h - y), c0 if (row + col) % 2 == 0 else c1)
            x += step
            col += 1
        y += step
        row += 1


class _SpriteView(QWidget):
    """整数倍放大的一格。可点、可拖入图片。"""

    clicked = pyqtSignal()
    dropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._img = None
        self._selected = False
        self._locked = False
        self._show_zoom = False
        self._hint = ""
        self._force_hint = False
        self._drop_icon = None
        self.setAcceptDrops(True)
        self.setMinimumSize(72, 72)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(False)

    def set_image(self, im) -> None:
        self._img = None if im is None else _pil_to_qimage(im)
        self.update()

    def set_selected(self, on: bool) -> None:
        self._selected = bool(on)
        self.update()

    def set_locked(self, on: bool) -> None:
        self._locked = bool(on)
        self.update()

    def set_hint(self, text: str) -> None:
        self._hint = text or ""
        self.update()

    def set_hint_visible(self, on: bool) -> None:
        self._force_hint = bool(on)
        self.update()

    single_drop = False      # 动效页：一次只收一个文件
    multi_refused = None     # 多文件被拒时的回调（页面给状态栏提示）
    project_drop = None      # 第十三阶段：动效页收 .pxa 工程的回调

    def _refuse_multi(self, event) -> bool:
        if not self.single_drop:
            return False
        md = event.mimeData()
        if md is None or not md.hasUrls() or len(md.urls()) <= 1:
            return False
        event.ignore()
        if callable(self.multi_refused):
            self.multi_refused()
        return True

    def dragEnterEvent(self, event):
        if self._refuse_multi(event):
            return
        if callable(self.project_drop) and _pxa_path(event.mimeData()):
            event.acceptProposedAction()
            return
        _accept_image_drag(event)

    def dragMoveEvent(self, event):
        if self.single_drop and event.mimeData() is not None and event.mimeData().hasUrls() and len(event.mimeData().urls()) > 1:
            event.ignore()
            return
        if callable(self.project_drop) and _pxa_path(event.mimeData()):
            event.acceptProposedAction()
            return
        _accept_image_drag(event)

    def dropEvent(self, event):
        if self._refuse_multi(event):
            return
        pxa = _pxa_path(event.mimeData()) if callable(self.project_drop) else None
        if pxa:
            event.acceptProposedAction()
            self.project_drop(pxa)
            return
        paths = _image_paths(event.mimeData())
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self.dropped.emit(paths[0])

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def _paint_border_and_hint(self, painter, empty):
        show_hint = empty or self._force_hint
        if show_hint and self.acceptDrops():
            paint_file_drop_outline(painter, self.rect(), "pixel_board", active=self._selected)
            if self._drop_icon is None:
                self._drop_icon = file_drop_pixmap(self)
            icon = self._drop_icon
            dpr = icon.devicePixelRatioF()
            left = int((self.width() - icon.width() / dpr) / 2)
            top = int((self.height() - icon.height() / dpr) / 2)
            painter.drawPixmap(left, top, icon)
            return

        color = QColor(tk("accent") if self._selected else tk("border"))
        painter.setPen(QPen(color, 2 if self._selected else 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self.rect().adjusted(1, 1, -2, -2))
        if self._hint and show_hint:
            font = QFont("等线")
            font.setPixelSize(13)
            painter.setFont(font)
            painter.setPen(QColor(tk("text_mut")))
            painter.drawText(self.rect(), int(Qt.AlignCenter), self._hint)

    def paintEvent(self, event):
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        p.fillRect(self.rect(), QColor(tk("panel_2")))
        zoom = 1
        if self._img is not None and not self._img.isNull():
            iw, ih = self._img.width(), self._img.height()
            zoom = max(1, min(self.width() // max(1, iw), self.height() // max(1, ih)))
            dw, dh = iw * zoom, ih * zoom
            if iw > self.width() or ih > self.height():   # 大图/扁长图缩小放进缩略框，保持宽高比
                f = min(self.width() / max(1, iw), self.height() / max(1, ih))
                dw, dh = max(1, int(iw * f)), max(1, int(ih * f))
            x = (self.width() - dw) // 2
            y = (self.height() - dh) // 2
            box = QRect(x, y, dw, dh)
            _paint_checker(p, box)
            p.drawImage(box, self._img)
        self._paint_border_and_hint(p, self._img is None or self._img.isNull())
        if self._locked:
            p.setPen(QColor(tk("accent")))
            font = QFont("等线")
            font.setPixelSize(11)
            p.setFont(font)
            p.drawText(8, 16, "改")
        if self._show_zoom and self._img is not None and not self._img.isNull():
            p.setPen(QColor(tk("text_dim")))
            font = QFont("等线")
            font.setPixelSize(11)
            p.setFont(font)
            p.drawText(8, self.height() - 8, f"×{zoom}")
        p.end()


_ZOOM_STEPS = (1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 32, 40)


class _PixelCanvas(_SpriteView):
    """能把鼠标落点换算成格子里的像素。滚轮缩放、中键或空格+左键平移、网格、坐标。"""

    stroke = pyqtSignal(int, int, bool)
    press_px = pyqtSignal(int, int, bool)
    move_px = pyqtSignal(int, int)
    release_px = pyqtSignal(int, int)
    space_tap = pyqtSignal()      # 空格按下又松开、中间没拖动：动效页拿来播放/停止

    def __init__(self, parent=None):
        super().__init__(parent)
        self._show_info = True     # 右下角 ×缩放/坐标；底图小格关掉
        self._space_used = False
        self._drag_paint = True
        self._pressed = False
        self._zoom = None          # None = 适应窗口（整数倍居中）
        self._ox = 0.0
        self._oy = 0.0
        self._grid = True
        self._hover = None
        self._pan_from = None
        self._space = False
        self._marquee = None       # (l, t, r, b) 像素，含两端
        self._ghosts = []          # [(QImage, 不透明度)]，画在当前图下面，只显示
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.NoContextMenu)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setCursor(Qt.CrossCursor)

    def set_drag_paint(self, on: bool) -> None:
        self._drag_paint = bool(on)

    def set_grid(self, on: bool) -> None:
        self._grid = bool(on)
        self.update()

    def set_marquee(self, rect) -> None:
        self._marquee = None if rect is None else tuple(int(v) for v in rect)
        self.update()

    def set_ghosts(self, items) -> None:
        """items: [(PIL 图, 不透明度)]。参考叠影、洋葱皮都走这里，不写进任何图层。"""
        self._ghosts = [(_pil_to_qimage(im), float(op)) for im, op in (items or []) if im is not None]
        self.update()

    def zoom_level(self) -> int:
        return self._geom()[0]

    def fit_view(self) -> None:
        self._zoom = None
        self.update()

    def _geom(self):
        if self._img is None or self._img.isNull():
            return 1, 0, 0
        iw, ih = self._img.width(), self._img.height()
        if self._zoom is None:
            zoom = max(1, min(self.width() // max(1, iw), self.height() // max(1, ih)))
            return zoom, (self.width() - iw * zoom) // 2, (self.height() - ih * zoom) // 2
        return self._zoom, int(round(self._ox)), int(round(self._oy))

    def zoom_by(self, step: int, anchor=None) -> None:
        """按档位缩放，anchor 是控件坐标；不给就以中心为准。"""
        if self._img is None or self._img.isNull():
            return
        zoom, ox, oy = self._geom()
        steps = list(_ZOOM_STEPS)
        if step > 0:
            bigger = [z for z in steps if z > zoom]
            nz = bigger[0] if bigger else steps[-1]
        else:
            smaller = [z for z in steps if z < zoom]
            nz = smaller[-1] if smaller else steps[0]
        if nz == zoom and self._zoom is not None:
            return
        ax = anchor.x() if anchor is not None else self.width() / 2.0
        ay = anchor.y() if anchor is not None else self.height() / 2.0
        u = (ax - ox) / float(zoom)
        v = (ay - oy) / float(zoom)
        self._zoom = nz
        self._ox = ax - u * nz
        self._oy = ay - v * nz
        self.update()

    def _cell_pos(self, pos, clamp_inside: bool = True):
        if self._img is None or self._img.isNull():
            return None
        zoom, ox, oy = self._geom()
        iw, ih = self._img.width(), self._img.height()
        fx = pos.x() - ox
        fy = pos.y() - oy
        x = int(fx // zoom)
        y = int(fy // zoom)
        if clamp_inside and (x < 0 or y < 0 or x >= iw or y >= ih):
            return None
        return x, y

    def _emit_stroke(self, event, drag: bool) -> None:
        if drag and not self._drag_paint:
            return
        xy = self._cell_pos(event.pos())
        if xy is None:
            return
        buttons = event.buttons()
        if not drag:
            buttons = buttons | event.button()
        self.stroke.emit(xy[0], xy[1], bool(buttons & Qt.RightButton))

    def event(self, event):
        # 空格归画布自己（按住拖动平移），不让页面级 Space 快捷键抢走
        if event.type() == QEvent.ShortcutOverride and event.key() == Qt.Key_Space and not event.modifiers():
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space:
            if not event.isAutoRepeat():
                self._space = True
                self._space_used = False
                self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_Space:
            if not event.isAutoRepeat():
                self._space = False
                self.setCursor(Qt.CrossCursor)
                if not self._space_used:
                    self.space_tap.emit()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        self._space = False
        self.setCursor(Qt.CrossCursor)
        super().focusOutEvent(event)

    def wheelEvent(self, event):
        dy = event.angleDelta().y()
        if not dy:
            event.ignore()
            return
        self.zoom_by(1 if dy > 0 else -1, event.pos())
        event.accept()

    def _start_pan(self, event) -> None:
        self._space_used = True
        zoom, ox, oy = self._geom()
        self._zoom = zoom
        self._ox, self._oy = float(ox), float(oy)
        self._pan_from = (event.pos(), self._ox, self._oy)
        self.setCursor(Qt.ClosedHandCursor)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self.fit_view()
            event.accept()
            return
        # 左右键双击按两次单击处理，免得丢笔
        self.mousePressEvent(event)

    def mousePressEvent(self, event):
        btn = event.button()
        if btn == Qt.MiddleButton or (btn == Qt.LeftButton and self._space):
            self._start_pan(event)
            event.accept()
            return
        if btn in (Qt.LeftButton, Qt.RightButton):
            self.setFocus(Qt.MouseFocusReason)
            self._pressed = True
            self.clicked.emit()
            xy = self._cell_pos(event.pos(), clamp_inside=False)
            if xy is not None:
                self.press_px.emit(xy[0], xy[1], btn == Qt.RightButton)
            self._emit_stroke(event, False)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pan_from is not None:
            start, ox, oy = self._pan_from
            self._ox = ox + event.pos().x() - start.x()
            self._oy = oy + event.pos().y() - start.y()
            self.update()
            event.accept()
            return
        hover = self._cell_pos(event.pos())
        if hover != self._hover:
            self._hover = hover
            self.update()
        if self._pressed:
            xy = self._cell_pos(event.pos(), clamp_inside=False)
            if xy is not None:
                self.move_px.emit(xy[0], xy[1])
            self._emit_stroke(event, True)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._pan_from is not None and event.button() in (Qt.MiddleButton, Qt.LeftButton):
            self._pan_from = None
            self.setCursor(Qt.OpenHandCursor if self._space else Qt.CrossCursor)
            event.accept()
            return
        if self._pressed:
            self._pressed = False
            xy = self._cell_pos(event.pos(), clamp_inside=False)
            if xy is not None:
                self.release_px.emit(xy[0], xy[1])
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        if self._hover is not None:
            self._hover = None
            self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        p.fillRect(self.rect(), QColor(tk("panel_2")))
        has_img = self._img is not None and not self._img.isNull()
        zoom = 1
        if has_img:
            zoom, ox, oy = self._geom()
            iw, ih = self._img.width(), self._img.height()
            box = QRect(ox, oy, iw * zoom, ih * zoom)
            p.save()
            p.setClipRect(box.intersected(self.rect()))
            _paint_checker(p, box, max(4, min(16, zoom * 2)))
            for ghost, opacity in self._ghosts:
                p.setOpacity(max(0.0, min(1.0, opacity)))
                p.drawImage(box, ghost)
            p.setOpacity(1.0)
            p.drawImage(box, self._img)
            if self._grid and zoom >= 6:
                grid = QColor(tk("border"))
                grid.setAlpha(110)
                p.setPen(QPen(grid, 1))
                vis = box.intersected(self.rect())
                x0 = max(0, (vis.left() - ox) // zoom)
                x1 = min(iw, (vis.right() - ox) // zoom + 1)
                y0 = max(0, (vis.top() - oy) // zoom)
                y1 = min(ih, (vis.bottom() - oy) // zoom + 1)
                for gx in range(x0, x1 + 1):
                    p.drawLine(ox + gx * zoom, vis.top(), ox + gx * zoom, vis.bottom())
                for gy in range(y0, y1 + 1):
                    p.drawLine(vis.left(), oy + gy * zoom, vis.right(), oy + gy * zoom)
            if self._marquee is not None:
                l, t, r, b = self._marquee
                mrect = QRect(ox + l * zoom, oy + t * zoom, (r - l + 1) * zoom - 1, (b - t + 1) * zoom - 1)
                pen = QPen(QColor(tk("accent")), 1, Qt.DashLine)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawRect(mrect)
            if self._hover is not None and zoom >= 3:
                hx, hy = self._hover
                hl = QColor(tk("accent"))
                p.setPen(QPen(hl, 1))
                p.setBrush(Qt.NoBrush)
                p.drawRect(ox + hx * zoom, oy + hy * zoom, zoom - 1, zoom - 1)
            p.restore()
        self._paint_border_and_hint(p, not has_img)
        if has_img and self._show_info:
            font = QFont("等线")
            font.setPixelSize(11)
            p.setFont(font)
            p.setPen(QColor(tk("text_dim")))
            text = f"×{zoom}"
            if self._hover is not None:
                text = f"{self._hover[0]},{self._hover[1]}  " + text
            p.drawText(self.rect().adjusted(6, 0, -6, -4), int(Qt.AlignRight | Qt.AlignBottom), text)
        p.end()


class _SwatchGrid(QWidget):
    """正方形色块：上面 4 行 × 16 = 64 个预设，下面一行 16 个自定义。
    预设：左键前景，右键背景。自定义：空槽点一下（或 Shift+点）存入前景色；Ctrl+点清除；有色时同预设。"""

    picked = pyqtSignal(tuple, bool)
    store_custom = pyqtSignal(int)
    clear_custom = pyqtSignal(int)

    COLS = 16
    ROWS = 4
    GAP = 5
    SIDE_MAX = 30   # 色块边长上限，宽屏时不至于大得夸张

    def __init__(self, parent=None):
        super().__init__(parent)
        self._colors = []
        self._custom = [None] * _CUSTOM_SLOTS
        self._fg = (0, 0, 0)
        self._bg = (255, 255, 255)
        self.setMinimumSize(self.COLS * 10, (self.ROWS + 1) * 10 + self.GAP)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setContextMenuPolicy(Qt.NoContextMenu)
        self.setToolTip("左键前景、右键背景；最下一行是自定义：空格点一下或 Shift+点 存入前景色，Ctrl+点 清除")

    def set_colors(self, colors, fg, bg, custom=None) -> None:
        self._colors = [tuple(c[:3]) for c in colors][:_PRESET_SWATCHES]
        if custom is not None:
            self._custom = [tuple(c[:3]) if c else None for c in custom][:_CUSTOM_SLOTS]
        self._fg = tuple(fg[:3])
        self._bg = tuple(bg[:3])
        self.update()

    def _layout(self):
        """按当前宽高挑列数（8/16/32），让 64 预设 + 16 自定义的正方形色块尽量大。返回 (列数, 预设行数, 边长)。"""
        best = None
        for cols in (8, 16, 32):
            prow = (_PRESET_SWATCHES + cols - 1) // cols
            crow = (_CUSTOM_SLOTS + cols - 1) // cols
            n = min(self.width() // cols, (self.height() - self.GAP) // (prow + crow), self.SIDE_MAX)
            if best is None or n > best[2]:
                best = (cols, prow, n, False)
        # 第九阶段：宽屏时自定义 16 格排成 4×4 放在预设右边，横向空间也用上
        n = min((self.width() - self.GAP) // (16 + 4), self.height() // 4, self.SIDE_MAX)
        if n > best[2]:
            best = (16, 4, n, True)
        return best[0], best[1], max(6, best[2]), best[3]

    def side(self) -> int:
        return self._layout()[2]

    def cell_rect(self, kind: str, index: int) -> QRect:
        cols, prow, n, beside = self._layout()
        if beside and kind == "custom":
            return QRect(cols * n + self.GAP + (index % 4) * n, (index // 4) * n, n, n)
        col = index % cols
        row = index // cols + (prow if kind == "custom" else 0)
        y = row * n + (self.GAP if kind == "custom" else 0)
        return QRect(col * n, y, n, n)

    def _hit(self, pos):
        for i in range(len(self._colors)):
            if self.cell_rect("preset", i).contains(pos):
                return "preset", i
        for i in range(_CUSTOM_SLOTS):
            if self.cell_rect("custom", i).contains(pos):
                return "custom", i
        return None, -1

    def mousePressEvent(self, event):
        kind, index = self._hit(event.pos())
        if kind is None:
            return
        bg = event.button() == Qt.RightButton
        if kind == "preset":
            self.picked.emit(self._colors[index], bg)
            return
        mods = event.modifiers()
        if mods & Qt.ControlModifier:
            self.clear_custom.emit(index)
        elif self._custom[index] is None or (mods & Qt.ShiftModifier):
            self.store_custom.emit(index)
        else:
            self.picked.emit(self._custom[index], bg)

    def paintEvent(self, event):
        del event
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(tk("panel_2")))
        items = [("preset", i, c) for i, c in enumerate(self._colors)]
        items += [("custom", i, c) for i, c in enumerate(self._custom)]
        for kind, i, rgb in items:
            rect = self.cell_rect(kind, i).adjusted(1, 1, -1, -1)
            if rgb is None:
                pen = QPen(QColor(tk("border")))
                pen.setStyle(Qt.DashLine)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawRect(rect.adjusted(0, 0, -1, -1))
                continue
            p.fillRect(rect, QColor(*rgb))
            if rgb == self._fg or rgb == self._bg:
                pen = QPen(QColor(tk("accent") if rgb == self._fg else tk("text")))
                pen.setWidth(2 if rgb == self._fg else 1)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawRect(rect.adjusted(0, 0, -1, -1))
        p.end()


def _grab_screens():
    """每块屏各截一张：[(逻辑坐标 QRect, 物理像素 QImage)]。高 DPI 时图比逻辑尺寸大，取色时按比例换算。"""
    shots = []
    for screen in QApplication.screens():
        try:
            pm = screen.grabWindow(0)
        except Exception:
            log.exception("截屏失败")
            continue
        if pm.isNull():
            continue
        shots.append((QRect(screen.geometry()), pm.toImage().convertToFormat(QImage.Format_RGB32)))
    return shots


class _PickerWindow(QWidget):
    """一块屏一个全屏窗口，铺上截图，带放大镜。"""

    def __init__(self, owner, geom: QRect, image: QImage):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self._owner = owner
        self._img = image
        self._pos = QPoint(geom.width() // 2, geom.height() // 2)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setGeometry(geom)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)

    def physical(self, pos: QPoint):
        w = max(1, self.width())
        h = max(1, self.height())
        x = int(pos.x() * self._img.width() / w)
        y = int(pos.y() * self._img.height() / h)
        return max(0, min(self._img.width() - 1, x)), max(0, min(self._img.height() - 1, y))

    def color_at(self, pos: QPoint) -> tuple:
        x, y = self.physical(pos)
        c = QColor(self._img.pixel(x, y))
        return (c.red(), c.green(), c.blue())

    def mouseMoveEvent(self, event):
        self._pos = event.pos()
        self.update()

    def mousePressEvent(self, event):
        bg = event.button() == Qt.RightButton or bool(event.modifiers() & Qt.ShiftModifier)
        self._owner.finish(self.color_at(event.pos()), bg)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._owner.cancel()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        del event
        p = QPainter(self)
        p.drawImage(self.rect(), self._img)
        zoom, half = 10, 5
        size = (half * 2 + 1) * zoom
        cx, cy = self.physical(self._pos)
        x0 = self._pos.x() + 20
        y0 = self._pos.y() + 20
        if x0 + size + 4 > self.width():
            x0 = self._pos.x() - 20 - size
        if y0 + size + 28 > self.height():
            y0 = self._pos.y() - 20 - size - 24
        p.fillRect(QRect(x0 - 2, y0 - 2, size + 4, size + 28), QColor(tk("panel")))
        for dy in range(-half, half + 1):
            for dx in range(-half, half + 1):
                px, py = cx + dx, cy + dy
                if 0 <= px < self._img.width() and 0 <= py < self._img.height():
                    col = QColor(self._img.pixel(px, py))
                else:
                    col = QColor(tk("panel_2"))
                p.fillRect(QRect(x0 + (dx + half) * zoom, y0 + (dy + half) * zoom, zoom, zoom), col)
        pen = QPen(QColor(tk("accent")))
        pen.setWidth(2)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRect(x0 + half * zoom, y0 + half * zoom, zoom, zoom))
        r, g, b = self.color_at(self._pos)
        p.setPen(QColor(tk("text")))
        p.drawText(QRect(x0, y0 + size + 2, size, 22), Qt.AlignCenter, f"#{r:02X}{g:02X}{b:02X}")
        tip = "左键取前景 · 右键或 Shift+左键取背景 · Esc 取消"
        p.fillRect(QRect(self.width() // 2 - 170, 8, 340, 24), QColor(tk("panel")))
        p.drawText(QRect(self.width() // 2 - 170, 8, 340, 24), Qt.AlignCenter, tip)
        p.end()


class _ScreenPicker(QWidget):
    """全屏吸管控制器。grabber 可注入（测试用），默认抓所有屏。先截图，再显示遮罩。"""

    picked = pyqtSignal(tuple, bool)
    canceled = pyqtSignal()

    def __init__(self, grabber=None, parent=None):
        super().__init__(parent)
        self.hide()
        self._grab = grabber or _grab_screens
        self.windows = []

    def start(self) -> bool:
        shots = self._grab() or []
        if not shots:
            return False
        for geom, image in shots:
            win = _PickerWindow(self, geom, image)
            self.windows.append(win)
        for win in self.windows:
            win.show()
        if self.windows:
            self.windows[0].raise_()
            self.windows[0].activateWindow()
            self.windows[0].setFocus()
        return True

    def _close_all(self) -> None:
        wins, self.windows = self.windows, []
        for win in wins:
            try:
                win.close()
            except RuntimeError:
                pass
        top = self.window()   # 焦点还给主窗口，快捷键继续可用
        if top is not None and top is not self:
            top.raise_()
            top.activateWindow()

    def finish(self, rgb, bg: bool) -> None:
        self._close_all()
        self.picked.emit(tuple(rgb), bool(bg))

    def cancel(self) -> None:
        self._close_all()
        self.canceled.emit()


def _draw_icon(p: QPainter, name: str, box: QRectF, color: QColor) -> None:
    """矢量小图标：16×16 设计坐标，按 box 缩放居中。"""
    p.save()
    side = min(box.width(), box.height())
    p.translate(box.center().x() - side / 2, box.center().y() - side / 2)
    p.scale(side / 16.0, side / 16.0)
    pen = QPen(color, 1.5)
    pen.setCosmetic(False)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    P = lambda pts: QPolygonF([QPointF(x, y) for x, y in pts])
    L = lambda a, b, c, d: p.drawLine(QPointF(a, b), QPointF(c, d))

    def head(x, y, dx, dy):
        # 箭头尖在 (x,y)，朝 (dx,dy)
        if dx:
            L(x, y, x - 3 * dx, y - 3); L(x, y, x - 3 * dx, y + 3)
        else:
            L(x, y, x - 3, y - 3 * dy); L(x, y, x + 3, y - 3 * dy)

    n = name
    if n == "pencil":
        p.drawPolygon(P([(2.5, 13.5), (3.5, 10), (11, 2.5), (13.5, 5), (6, 12.5)]))
        L(3.5, 10, 6, 12.5); L(9.5, 4, 12, 6.5)
    elif n == "eraser":
        p.drawPolygon(P([(2.5, 9.5), (8.5, 3.5), (13.5, 8.5), (7.5, 14.5)]))
        p.setBrush(color); p.drawPolygon(P([(2.5, 9.5), (5.5, 6.5), (10.5, 11.5), (7.5, 14.5)])); p.setBrush(Qt.NoBrush)
    elif n == "fill":
        p.drawPolygon(P([(2, 8), (7, 3), (12, 8), (7, 13)]))
        L(2, 8, 12, 8)
        p.setBrush(color); p.setPen(Qt.NoPen); p.drawEllipse(QPointF(13.5, 12), 1.6, 2.0)
    elif n == "line":
        L(3, 13, 13, 3)
        p.setBrush(color); p.drawRect(QRectF(1.5, 11.5, 3, 3)); p.drawRect(QRectF(11.5, 1.5, 3, 3))
    elif n == "rect":
        p.drawRect(QRectF(2.5, 4, 11, 8))
    elif n == "ellipse":
        p.drawEllipse(QRectF(2, 3.5, 12, 9))
    elif n == "select":
        pen.setDashPattern([2, 1.5]); p.setPen(pen); p.drawRect(QRectF(2.5, 3.5, 11, 9))
    elif n == "shape_fill":
        p.setBrush(color); p.drawRect(QRectF(2.5, 4, 11, 8))
    elif n == "mirror":
        dash = QPen(pen); dash.setDashPattern([1.5, 1.5]); p.setPen(dash); L(8, 1.5, 8, 14.5); p.setPen(pen)
        p.setBrush(color); p.drawPolygon(P([(6, 4), (6, 12), (1.5, 12)])); p.setBrush(Qt.NoBrush)
        p.drawPolygon(P([(10, 4), (10, 12), (14.5, 12)]))
    elif n.startswith("brush"):
        k = int(n[-1]); w = 2.5 + 3 * k
        p.setPen(Qt.NoPen); p.setBrush(color); p.drawRect(QRectF(8 - w / 2, 8 - w / 2, w, w))
    elif n == "grid":
        p.drawRect(QRectF(2, 2, 12, 12)); L(6, 2, 6, 14); L(10, 2, 10, 14); L(2, 6, 14, 6); L(2, 10, 14, 10)
    elif n == "fit":
        for (x, y, dx, dy) in ((2, 2, 1, 1), (14, 2, -1, 1), (2, 14, 1, -1), (14, 14, -1, -1)):
            L(x, y, x + 4 * dx, y); L(x, y, x, y + 4 * dy)
        p.drawRect(QRectF(6, 6, 4, 4))
    elif n in ("undo", "redo"):
        f = (lambda x: x) if n == "undo" else (lambda x: 16 - x)
        path = QPainterPath(QPointF(f(3), 6.5))
        path.lineTo(f(10), 6.5)
        path.cubicTo(QPointF(f(14.5), 6.5), QPointF(f(14.5), 13.5), QPointF(f(10), 13.5))
        path.lineTo(f(6), 13.5)
        p.drawPath(path)
        L(f(3), 6.5, f(6), 3.5); L(f(3), 6.5, f(6), 9.5)
    elif n in ("left", "right", "up", "down"):
        dx, dy = {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1)}[n]
        L(8 - 5 * dx, 8 - 5 * dy, 8 + 5 * dx, 8 + 5 * dy); head(8 + 5 * dx, 8 + 5 * dy, dx, dy)
    elif n == "swap":
        L(2.5, 5, 13, 5); head(13, 5, 1, 0); L(13.5, 11, 3, 11); head(3, 11, -1, 0)
    elif n == "picker":
        thick = QPen(pen); thick.setWidthF(2.2); p.setPen(thick); L(3, 13, 9, 7); p.setPen(pen)
        L(7.5, 5.5, 10.5, 8.5)
        p.setBrush(color); p.drawEllipse(QPointF(11.5, 4.5), 2.6, 2.6)
    elif n == "store":
        L(8, 2, 8, 10); head(8, 10, 0, 1)
        path = QPainterPath(QPointF(2.5, 10)); path.lineTo(2.5, 14); path.lineTo(13.5, 14); path.lineTo(13.5, 10); p.drawPath(path)
    elif n == "save":
        p.drawRect(QRectF(2.5, 2.5, 11, 11)); p.drawRect(QRectF(5, 2.5, 6, 3.5)); p.drawRect(QRectF(4.5, 9, 7, 4.5))
    elif n == "add":
        L(8, 3, 8, 13); L(3, 8, 13, 8)
    elif n == "delete":
        L(2.5, 4, 13.5, 4); L(6, 2, 10, 2)
        p.drawPolygon(P([(4, 5), (5, 14), (11, 14), (12, 5)])); L(8, 7, 8, 12)
    elif n in ("layer_up", "layer_down"):
        s = -1 if n == "layer_up" else 1
        L(3, 8 - 2.5 * s, 8, 8 + 2.5 * s); L(8, 8 + 2.5 * s, 13, 8 - 2.5 * s)
        L(3, 8 - 2.5 * s - 4 * s, 8, 8 + 2.5 * s - 4 * s); L(8, 8 + 2.5 * s - 4 * s, 13, 8 - 2.5 * s - 4 * s)
    elif n in ("play", "stop"):
        p.setBrush(color)
        if n == "play":
            p.drawPolygon(P([(4.5, 2.5), (13, 8), (4.5, 13.5)]))
        else:
            p.drawRect(QRectF(4, 4, 8, 8))
    elif n == "loop":
        path = QPainterPath(QPointF(4, 10.5))
        path.arcTo(QRectF(2, 3.5, 12, 9), 200, -290)
        p.drawPath(path); head(12.2, 4.4, 1, 0)
    elif n == "pingpong":
        L(2.5, 5.5, 13, 5.5); head(13, 5.5, 1, 0); L(13.5, 10.5, 3, 10.5); head(3, 10.5, -1, 0)
        p.setBrush(color); p.drawEllipse(QPointF(8, 8), 1.2, 1.2)
    elif n == "onion":
        # 前红后蓝：和编辑器里洋葱皮的颜色一致
        p.setBrush(QColor(230, 80, 80, 210)); p.drawEllipse(QPointF(5.8, 8), 4.2, 4.2)
        p.setBrush(QColor(80, 140, 250, 210)); p.drawEllipse(QPointF(10.2, 8), 4.2, 4.2)
    elif n.startswith("pips"):
        k = int(n[-1]); p.setBrush(color); p.setPen(Qt.NoPen)
        for i in range(k):
            x = 8 - (k - 1) * 2.5 + i * 5
            p.drawEllipse(QPointF(x, 8), 1.9, 1.9)
    elif n.startswith("hold"):
        k = int(n[-1]); p.setBrush(color); p.setPen(Qt.NoPen)
        w = 2.4; gap = 1.2; total = k * w + (k - 1) * gap; x0 = 8 - total / 2
        for i in range(k):
            p.drawRect(QRectF(x0 + i * (w + gap), 4, w, 8))
    elif n == "dup":
        p.drawRect(QRectF(2.5, 5, 8, 8.5)); p.drawRect(QRectF(5.5, 2.5, 8, 8.5))
    elif n == "copy_base":
        p.drawRect(QRectF(8.5, 6.5, 6, 7)); p.setBrush(color); p.drawRect(QRectF(1.5, 1.5, 5, 5)); p.setBrush(Qt.NoBrush)
        L(4, 8, 4, 10); L(4, 10, 7.5, 10); head(7.5, 10, 1, 0)
    elif n in ("expand", "shrink"):
        s = 1 if n == "expand" else -1
        if s > 0:
            L(3, 13, 7, 9); L(3, 13, 3, 9.5); L(3, 13, 6.5, 13); L(13, 3, 9, 7); L(13, 3, 13, 6.5); L(13, 3, 9.5, 3)
        else:
            L(7, 9, 3, 13); L(7, 9, 7, 12.5); L(7, 9, 3.5, 9); L(9, 7, 13, 3); L(9, 7, 9, 3.5); L(9, 7, 12.5, 7)
    elif n == "proj_new":
        p.drawPolygon(P([(3.5, 1.5), (9.5, 1.5), (12.5, 4.5), (12.5, 14.5), (3.5, 14.5)]))
        L(8, 6.5, 8, 11.5); L(5.5, 9, 10.5, 9)
    elif n == "proj_open":
        p.drawPolygon(P([(1.5, 3.5), (6, 3.5), (7.5, 5), (13.5, 5), (13.5, 13.5), (1.5, 13.5)]))
        L(1.5, 7.5, 13.5, 7.5)
    elif n == "save_as":
        p.drawRect(QRectF(1.5, 1.5, 10, 10)); p.drawRect(QRectF(4, 1.5, 5, 3)); L(9.5, 15, 15, 9.5)
        p.setBrush(color); p.drawPolygon(P([(9.5, 15), (8.7, 12.6), (10.3, 12.6)])); p.setBrush(Qt.NoBrush)
    elif n == "shift_all":
        p.drawRect(QRectF(2.5, 4.5, 7, 7)); p.drawRect(QRectF(5, 2, 7, 7))
        L(9, 13.5, 14, 13.5); head(14, 13.5, 1, 0)
    elif n in ("eye", "eye_off"):
        path = QPainterPath(QPointF(1.5, 8))
        path.quadTo(QPointF(8, 1.5), QPointF(14.5, 8)); path.quadTo(QPointF(8, 14.5), QPointF(1.5, 8))
        p.drawPath(path)
        if n == "eye":
            p.setBrush(color); p.drawEllipse(QPointF(8, 8), 2.2, 2.2)
        else:
            L(2.5, 13.5, 13.5, 2.5)
    p.restore()


class _IconButton(QPushButton):
    """自绘矢量图标的方块按钮；不吃 _BTN_QSS（尺寸见 _ICON_SIZES），选中时 accent 底。"""

    def __init__(self, icon: str, tip: str = "", size: str = "tool", checkable: bool = False, parent=None):
        super().__init__("", parent)
        self.icon_name = icon
        self._px = _ICON_SIZES[size]
        self.setProperty("pixelIcon", size)
        self.setCheckable(checkable)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(self._px, self._px)
        if tip:
            self.setToolTip(tip)
            self.setAccessibleName(tip.split()[0])

    def set_icon(self, icon: str) -> None:
        self.icon_name = icon
        self.update()

    def sizeHint(self):
        return QSize(self._px, self._px)

    def minimumSizeHint(self):
        return QSize(self._px, self._px)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        on = self.isChecked() or self.isDown()
        hover = self.underMouse() and self.isEnabled()
        if on:
            p.setPen(QPen(QColor(tk("accent")), 1))
            p.setBrush(QColor(tk("accent")))
            fg = QColor("#FFFFFF")
        else:
            bg = QColor(tk("panel"))
            p.setPen(QPen(QColor(tk("accent") if hover else tk("border")), 1))
            p.setBrush(bg)
            fg = QColor(tk("accent") if hover else tk("text"))
        if not self.isEnabled():
            fg = QColor(tk("text_mut"))
        radius = 4 if self._px <= 22 else 6
        p.drawRoundedRect(r, radius, radius)
        inset = 4 if self._px <= 22 else 6
        _draw_icon(p, self.icon_name, QRectF(self.rect()).adjusted(inset, inset, -inset, -inset), fg)
        p.end()


class _LayerThumb(QWidget):
    """图层预览 32×32：棋盘底，等比缩放居中（非正方形格子保持比例）。"""

    SIDE = 32

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIDE, self.SIDE)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._qimg = None
        self._key = None
        self._opacity = 1.0
        self._visible = True

    def set_layer(self, layer: dict) -> bool:
        """有变化才重做缩略图；返回是否真的刷新了。"""
        im = layer["image"]
        key = (im.size, zlib.crc32(im.tobytes()), round(float(layer.get("opacity", 1)), 3), bool(layer.get("visible", True)))
        if key == self._key:
            return False
        self._key = key
        self._opacity = float(layer.get("opacity", 1))
        self._visible = bool(layer.get("visible", True))
        w, h = im.size
        scale = min(self.SIDE / max(1, w), self.SIDE / max(1, h))
        tw, th = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
        self._qimg = _pil_to_qimage(im).scaled(tw, th, Qt.IgnoreAspectRatio, Qt.FastTransformation)
        self.update()
        return True

    def paintEvent(self, event):
        del event
        p = QPainter(self)
        if self._qimg is not None:
            x = (self.SIDE - self._qimg.width()) // 2
            y = (self.SIDE - self._qimg.height()) // 2
            _paint_checker(p, QRect(x, y, self._qimg.width(), self._qimg.height()), 4)
            p.setOpacity(max(0.0, min(1.0, self._opacity)) * (1.0 if self._visible else 0.25))
            p.drawImage(x, y, self._qimg)
            p.setOpacity(1.0)
        else:
            _paint_checker(p, self.rect(), 4)
        p.setPen(QPen(QColor(tk("border"))))
        p.setBrush(Qt.NoBrush)
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.end()


class _LayerRow(QFrame):
    clicked = pyqtSignal(int)
    rename = pyqtSignal(int)

    def __init__(self, index: int, parent=None):
        super().__init__(parent)
        self.index = index
        self.setObjectName("PixelLayerRow")
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.index)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.rename.emit(self.index)
        super().mouseDoubleClickEvent(event)


class _ColorWell(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rgba = (0, 0, 0, 255)
        self.setFixedSize(36, 36)

    def set_rgba(self, rgba) -> None:
        self._rgba = tuple(rgba)
        self.update()

    def paintEvent(self, event):
        del event
        p = QPainter(self)
        _paint_checker(p, self.rect(), 6)
        r, g, b, a = (list(self._rgba) + [255])[:4]
        if a:
            col = QColor(r, g, b, a)
            p.fillRect(self.rect().adjusted(2, 2, -2, -2), col)
        pen = QPen(QColor(tk("border")))
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.end()


class _DirCell(QWidget):
    clicked = pyqtSignal(str)
    dropped = pyqtSignal(str, str)
    save_clicked = pyqtSignal(str)
    stroke = pyqtSignal(str, int, int, bool)
    press_px = pyqtSignal(str, int, int, bool)
    move_px = pyqtSignal(str, int, int)
    release_px = pyqtSignal(str, int, int)

    def __init__(self, direction: str, parent=None):
        super().__init__(parent)
        self.direction = direction
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.view = _DirView(direction)
        self.view.setMinimumSize(64, 64)
        self.view.setToolTip("拖入图片到这个方向")
        self.view.clicked.connect(lambda: self.clicked.emit(self.direction))
        self.view.dropped.connect(lambda path: self.dropped.emit(self.direction, path))
        # 第九阶段：方向名和保存图标叠在缩略图角上，不再单占一行
        self.lbl = QLabel(DIR_LABEL[direction], self.view)
        self.lbl.setAlignment(Qt.AlignCenter)
        self.lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.btn_save = _IconButton("save", f"保存「{DIR_LABEL[direction]}」PNG（合成图，原像素）", "corner", parent=self.view)
        self.btn_save.clicked.connect(lambda: self.save_clicked.emit(self.direction))
        lay.addWidget(self.view, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_overlay()

    def showEvent(self, event):
        super().showEvent(event)
        self._place_overlay()

    def _place_overlay(self) -> None:
        self.lbl.adjustSize()
        self.lbl.move(3, 3)
        self.btn_save.move(max(0, self.view.width() - self.btn_save.width() - 3), 3)
        self.lbl.raise_()
        self.btn_save.raise_()

    def restyle(self) -> None:
        color = tk("accent") if self.view._selected else tk("text")
        weight = "font-weight:600;" if self.view._selected else ""
        self.lbl.setStyleSheet(
            f"color:{color};background:{tk('panel')};border:1px solid {tk('border')};border-radius:3px;"
            f"padding:0px 4px;font-size:12px;{weight}"
        )
        self.lbl.adjustSize()
        self.view.update()


class _DirView(_SpriteView):
    """第十五阶段：画板四向缩略图。点选方向；按住拖到另一格 = 两个方向的内容（全部图层）互换，方向名不动。"""

    swap = pyqtSignal(str, str)    # (被拖的方向, 落到的方向)

    def __init__(self, direction: str, parent=None):
        super().__init__(parent)
        self.direction = direction
        self._press_at = None
        self._drop_hl = False

    @staticmethod
    def _dir_of(event):
        md = event.mimeData()
        if md is None or not md.hasFormat(_DIR_MIME):
            return None
        return bytes(md.data(_DIR_MIME)).decode("ascii", "ignore")

    def dragEnterEvent(self, event):
        src = self._dir_of(event)
        if src is not None:
            event.acceptProposedAction()
            self._drop_hl = src != self.direction
            self.update()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._dir_of(event) is not None:
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self._drop_hl = False
        self.update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        src = self._dir_of(event)
        if src is not None:
            self._drop_hl = False
            self.update()
            event.acceptProposedAction()
            if src in DIRS and src != self.direction:
                self.swap.emit(src, self.direction)
            return
        super().dropEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_at = event.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._press_at = None
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_at is None or not (event.buttons() & Qt.LeftButton):
            return super().mouseMoveEvent(event)
        if (event.pos() - self._press_at).manhattanLength() < QApplication.startDragDistance():
            return
        self._press_at = None
        drag = QDrag(self)
        drag.setMimeData(self.drag_mime())
        drag.setPixmap(self.drag_pixmap())
        drag.setHotSpot(QPoint(28, 28))
        drag.exec_(Qt.MoveAction)

    def drag_mime(self) -> QMimeData:
        md = QMimeData()
        md.setData(_DIR_MIME, self.direction.encode("ascii"))
        return md

    def drag_pixmap(self):
        pm = self.grab().scaled(56, 56, Qt.KeepAspectRatio, Qt.FastTransformation)
        p = QPainter(pm)
        p.setPen(QPen(QColor(tk("accent")), 2))
        p.drawRect(1, 1, pm.width() - 2, pm.height() - 2)
        p.end()
        return pm

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._drop_hl:
            p = QPainter(self)
            c = QColor(tk("accent"))
            p.setPen(QPen(c, 3, Qt.DashLine))
            c.setAlpha(40)
            p.setBrush(c)
            p.drawRect(self.rect().adjusted(2, 2, -3, -3))
            p.end()


class _FrameView(_SpriteView):
    """帧缩略图：点选、拖动重排（本页内部 mime），也能拖入图片替换。"""

    reorder = pyqtSignal(int)    # 有别的帧拖到我身上：参数是被拖的帧序号

    def __init__(self, index: int, parent=None):
        super().__init__(parent)
        self.index = index
        self._press_at = None

    def _is_frame_drag(self, event) -> bool:
        md = event.mimeData()
        return md is not None and md.hasFormat(_FRAME_MIME)

    def dragEnterEvent(self, event):
        if self._is_frame_drag(event):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._is_frame_drag(event):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if self._is_frame_drag(event):
            try:
                src = int(bytes(event.mimeData().data(_FRAME_MIME)).decode("ascii"))
            except ValueError:
                event.ignore()
                return
            event.acceptProposedAction()
            if src != self.index:
                self.reorder.emit(src)
            return
        super().dropEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_at = event.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._press_at = None
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_at is None or not (event.buttons() & Qt.LeftButton):
            return super().mouseMoveEvent(event)
        if (event.pos() - self._press_at).manhattanLength() < QApplication.startDragDistance():
            return
        self._press_at = None
        md = QMimeData()
        md.setData(_FRAME_MIME, str(self.index).encode("ascii"))
        drag = QDrag(self)
        drag.setMimeData(md)
        drag.setPixmap(self.grab().scaled(40, 40, Qt.KeepAspectRatio, Qt.FastTransformation))
        drag.setHotSpot(QPoint(20, 20))
        drag.exec_(Qt.MoveAction)


class _FrameCell(QWidget):
    """时间轴一格：64×64 缩略图，左上序号、右下时长角标（×2…），悬停出现 复制/删除。"""

    SIDE = 64
    clicked = pyqtSignal(int)
    dropped = pyqtSignal(int, str)
    reorder = pyqtSignal(int, int)        # (被拖的帧, 落到的帧)
    dup_clicked = pyqtSignal(int)
    del_clicked = pyqtSignal(int)
    move_clicked = pyqtSignal(int, int)   # (帧, ±1)

    def __init__(self, index: int, parent=None):
        super().__init__(parent)
        self.index = index
        self.setFixedSize(self.SIDE, self.SIDE)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = _FrameView(index)
        self.view.setMinimumSize(self.SIDE, self.SIDE)
        self.view.setToolTip("拖入图片到这一帧")
        self.view.clicked.connect(lambda: self.clicked.emit(self.index))
        self.view.dropped.connect(lambda path: self.dropped.emit(self.index, path))
        self.view.reorder.connect(lambda src: self.reorder.emit(src, self.index))
        lay.addWidget(self.view)
        self.lbl = QLabel("", self.view)          # 序号
        self.lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.lbl_hold = QLabel("", self.view)     # 时长角标
        self.lbl_hold.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.btn_dup = _IconButton("dup", "复制这一帧（插在后面）", "corner", parent=self.view)
        self.btn_del = _IconButton("delete", "删除这一帧", "corner", parent=self.view)
        self.btn_dup.clicked.connect(lambda: self.dup_clicked.emit(self.index))
        self.btn_del.clicked.connect(lambda: self.del_clicked.emit(self.index))
        self._hold = 1
        self._set_hover(False)

    def set_info(self, hold: int) -> None:
        self._hold = int(hold)
        self.lbl.setText(f"{self.index + 1:02d}")
        self.lbl_hold.setText(f"×{self._hold}" if self._hold > 1 else "")
        self.lbl_hold.setVisible(self._hold > 1)
        self._place()

    def _place(self) -> None:
        self.lbl.adjustSize()
        self.lbl.move(3, 3)
        self.lbl_hold.adjustSize()
        self.lbl_hold.move(self.SIDE - self.lbl_hold.width() - 3, self.SIDE - self.lbl_hold.height() - 3)
        c = _ICON_SIZES["corner"]
        self.btn_del.move(self.SIDE - c - 2, 2)
        self.btn_dup.move(self.SIDE - 2 * c - 3, 2)
        for w in (self.lbl, self.lbl_hold, self.btn_dup, self.btn_del):
            w.raise_()

    def _set_hover(self, on: bool) -> None:
        self.btn_dup.setVisible(on)
        self.btn_del.setVisible(on)

    def enterEvent(self, event):
        self._set_hover(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._set_hover(False)
        super().leaveEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        acts = {
            menu.addAction("复制这一帧"): lambda: self.dup_clicked.emit(self.index),
            menu.addAction("删除这一帧"): lambda: self.del_clicked.emit(self.index),
            menu.addAction("往前挪  Alt+←"): lambda: self.move_clicked.emit(self.index, -1),
            menu.addAction("往后挪  Alt+→"): lambda: self.move_clicked.emit(self.index, 1),
        }
        act = menu.exec_(event.globalPos())
        if act in acts:
            acts[act]()

    def restyle(self) -> None:
        sel = self.view._selected
        self.lbl.setStyleSheet(
            f"color:{'#ffffff' if sel else tk('text')};background:{tk('accent') if sel else tk('panel')};"
            f"border:1px solid {tk('accent') if sel else tk('border')};border-radius:3px;padding:0px 3px;"
            f"font-size:11px;{'font-weight:600;' if sel else ''}"
        )
        self.lbl_hold.setStyleSheet(
            f"color:#ffffff;background:{tk('warn')};border-radius:3px;padding:0px 3px;font-size:11px;font-weight:600;"
        )
        self._place()
        self.view.update()


class _WheelGuard(QObject):
    """挡掉宽高框/颜色下拉上的滚轮：鼠标滚过就改格子或减色会改坏画面。"""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Wheel:
            event.ignore()
            return True
        return False


class _AlignGrid(QWidget):
    """3×3 九宫格位置选择：左上 … 右下，默认下中。接口仿 QComboBox：currentText / setCurrentText。"""

    changed = pyqtSignal(str)

    def __init__(self, value: str = ALIGN_DEFAULT, parent=None):
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        self.buttons = {}
        for i, key in enumerate(ALIGNS):
            b = QPushButton("")
            b.setCheckable(True)
            b.setFixedSize(18, 18)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(key)
            b.setAccessibleName(key)
            b.setProperty("alignCell", True)
            b.clicked.connect(lambda _=False, k=key: self.setCurrentText(k))
            self.buttons[key] = b
            grid.addWidget(b, i // 3, i % 3)
        self._value = ""
        self.setCurrentText(value)

    def currentText(self) -> str:
        return self._value

    def setCurrentText(self, value) -> None:
        self._value = norm_align(value)
        for k, b in self.buttons.items():
            b.setChecked(k == self._value)
        self.changed.emit(self._value)


class _ImportDialog(QDialog):
    """动效页导入的小弹窗：一段说明 + 若干按钮（第一个默认）+ 对齐下拉 + 可选“本批都这样”。"""

    def __init__(self, parent, title: str, text: str, buttons, align: str, remember: bool = False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.choice = None
        self.setStyleSheet(
            f"QDialog{{background:{tk('panel')};}}"
            f"QLabel,QCheckBox{{color:{tk('text')};font-size:12px;}}"
            f"QPushButton{{{_BTN_QSS}color:{tk('text')};background:{tk('panel_2')};border:1px solid {tk('border')};border-radius:3px;min-height:20px;}}"
            f"QPushButton:hover,QPushButton:default{{border-color:{tk('accent')};}}"
            f"QPushButton:default{{background:{tk('accent')};color:#ffffff;}}"
            f"QPushButton[alignCell=\"true\"]{{padding:0px;min-height:0px;background:{tk('panel_2')};border:1px solid {tk('border')};border-radius:2px;}}"
            f"QPushButton[alignCell=\"true\"]:checked{{background:{tk('accent')};border-color:{tk('accent')};}}"
        )
        self.setMinimumWidth(320)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        self.lbl = QLabel(text)
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl)
        row = QHBoxLayout()
        self.lbl_align = QLabel("对齐：")
        row.addWidget(self.lbl_align)
        self.align = _AlignGrid(align)
        self.align.setToolTip("图片与母板尺寸不同时放在母板的哪个位置；默认下中＝脚贴底")
        self.lbl_align_name = QLabel(self.align.currentText())
        self.align.changed.connect(self.lbl_align_name.setText)
        row.addWidget(self.align)
        row.addWidget(self.lbl_align_name)
        row.addStretch(1)
        lay.addLayout(row)
        self.chk = QCheckBox("本批后续图片都这样处理，不再询问")
        self.chk.setVisible(remember)
        lay.addWidget(self.chk)
        btns = QVBoxLayout() if len(buttons) > 3 else QHBoxLayout()
        btns.setSpacing(6)
        self.buttons = {}
        for i, (key, label) in enumerate(buttons):
            b = QPushButton(label)
            b.setCursor(Qt.PointingHandCursor)
            b.setAutoDefault(i == 0)
            b.setDefault(i == 0)
            b.clicked.connect(lambda _=False, k=key: self._pick(k))
            self.buttons[key] = b
            btns.addWidget(b)
        lay.addLayout(btns)

    def _pick(self, key) -> None:
        self.choice = key
        self.accept()

    def result_dict(self) -> dict:
        return {"choice": self.choice, "align": self.align.currentText(), "remember": self.chk.isChecked()}


class PagePixelBoard(QWidget):
    def hasHeightForWidth(self):
        return False

    def minimumSizeHint(self):
        return QSize(0, 0)

    def __init__(self):
        super().__init__()
        self.setObjectName("PageRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._art_cell = (32, 32)    # (宽, 高)
        self._art_colors = 0
        self._walk_cell = (32, 32)   # (宽, 高)
        self._walk_colors = 0
        self._art_dir = "down"
        self._board_order = ("down", "left", "up", "right")
        self._wheel_guard = _WheelGuard(self)
        self._layers = {}
        self._active_layer = {}
        for direction in DIRS:
            self._layers[direction] = [self._make_layer("底层")]
            self._active_layer[direction] = 0
        self._layer_sig_cache = None
        self._base_im = blank_cell(self._walk_cell)
        self._frames = [blank_cell(self._walk_cell) for _ in range(3)]
        self._frame_i = 0
        self._tool = "pencil"
        self._fg = (26, 28, 44, 255)
        self._bg = (244, 244, 244, 255)
        self._palette_name = "默认64"
        self._custom_colors = [None] * _CUSTOM_SLOTS
        self._custom_cell = {"art": None, "walk": None}
        self._screen_grabber = None   # 测试可注入假截图
        self._picker = None
        self._keep_stride = 2
        self._keep_leg = 42
        self._keep_four = False
        self._fps = 8
        self._art_dollar = True
        self._walk_dollar = True
        self._art_name = "画板"
        self._walk_name = "角色"
        self._save_path = ""
        self._play_at = 0
        self._art_tone = "mut"
        self._walk_tone = "mut"
        self._loading = False
        self._prefs_dirty = None
        # 共用工具栏状态
        self._shape_fill = False
        self._mirror_draw = False
        self._grid_on = True
        self._brush = 1
        self._walk_focus = "frame"     # 动效页当前画的是 base 还是 frame
        self._undo = {}                # key -> [(序号, 图)]
        self._redo = {}
        self._gundo = {"art": [], "walk": []}   # 全局快照（改格子/改颜色/复制）
        self._gredo = {"art": [], "walk": []}
        self._seq = 0                  # 每次可撤销操作的全局序号，保证撤销按时间顺序
        self._drag = None              # 一次按下到松开
        self._sel = None               # 选区状态
        # 第二阶段
        self._frame_hold = [1, 1, 1]
        self._walk_align = ALIGN_DEFAULT  # 第十二阶段：九宫格：尺寸不同时的对齐
        self._walk_shift_all = False      # 整图平移是否作用到全部帧
        self._import_hook = None          # 测试注入：hook(kind, info) -> dict(choice, align, remember) / 组名
        self._import_policy = None        # 本批“都这样”记下的选择   # 与 _frames 平行的每帧时长倍数
        self._onion_on = False
        self._onion_n = 1
        self._play_mode = "loop"       # loop / pingpong
        self._preview_big = False      # 第十阶段：预览栏放大（设置键 walk_preview_big）
        # 第十三/十四阶段：工程文件（动效 .pxa / 画板 .pxb）；path 空 = 未命名，sig = 上次新建/打开/保存时的内容指纹
        self._proj = {s: {"path": "", "sig": None, "dirty": False} for s in ("walk", "art")}
        self._project_prompt_hook = None   # 测试注入：hook(动作) -> "save" / "discard" / "cancel"
        self._project_path_hook = None     # 测试注入：hook("open"/"save") -> 路径或 ""
        self._dirty_timer = QTimer(self)
        self._dirty_timer.setSingleShot(True)
        self._dirty_timer.setInterval(250)
        self._dirty_timer.timeout.connect(self._update_dirty)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("VideoTabWidget")
        self.tabs.setIconSize(QSize(20, 20))
        self._install_tab_bar()
        self.page_art = self._make_tab_page("TabPixelArt")
        self.page_walk = self._make_tab_page("TabPixelWalk")
        self._build_art()
        self._build_walk()
        self.tabs.blockSignals(True)
        self.tabs.addTab(self.page_art, "画板")
        self.tabs.addTab(self.page_walk, "动效")
        self.tabs.setTabToolTip(0, _ART_SPEC)
        self.tabs.setTabToolTip(1, _WALK_SPEC)
        self.tabs.setCurrentIndex(0)
        self.tabs.blockSignals(False)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        root.addWidget(self.tabs, 1)
        self.palette = self._build_palette()
        root.addWidget(self.palette, 0)
        self.tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        # 调色盘区只占自己需要的高度，竖向空间都给画布
        self.palette.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.setFocusPolicy(Qt.ClickFocus)
        self._install_shortcuts()

        self._apply_tab_chrome()
        self._apply_palette_chrome()
        self._set_tool("pencil")
        self._style_choices()
        self._sync_art()
        self._sync_walk()
        self._sync_palette()
        self._sync_toolbar()
        self._mark_project_clean()
        theme.changed.connect(self.refresh_theme)

    def _make_layer(self, name: str) -> dict:
        return {
            "name": name,
            "image": blank_cell(getattr(self, "_art_cell", (32, 32))),
            "opacity": 1.0,
            "visible": True,
        }

    def set_prefs_dirty_callback(self, fn) -> None:
        self._prefs_dirty = fn

    def shutdown(self) -> None:
        self._timer.stop()

    def hideEvent(self, event) -> None:
        # 切到别的页面时停掉预览播放，免得后台空转
        timer = getattr(self, "_timer", None)
        if timer is not None and timer.isActive():
            self._stop_play()
        super().hideEvent(event)

    def _touch(self) -> None:
        if self._loading:
            return
        self._walk_changed()
        fn = self._prefs_dirty
        if callable(fn):
            fn()

    def _install_tab_bar(self) -> None:
        try:
            from ui_main import install_tight_pre_dl_tab_bar
            install_tight_pre_dl_tab_bar(self.tabs)
            setter = getattr(self.tabs.tabBar(), "set_info_slot", None)
            if callable(setter):
                setter(False)
        except Exception:
            log.exception("像素画板页签条安装失败")
            try:
                self.tabs.tabBar().setElideMode(Qt.ElideNone)
                self.tabs.tabBar().setExpanding(False)
            except Exception:
                pass

    def _make_tab_page(self, object_name: str) -> QWidget:
        page = QWidget()
        page.setObjectName(object_name)
        page.setAttribute(Qt.WA_StyledBackground, True)
        page.setAutoFillBackground(True)
        page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        return page

    def _tab_qss(self) -> str:
        bg = tk("panel_2")
        return (
            fmt(TAB_QSS + VIDEO_TAB_QSS)
            + "\n#PageRoot{background:transparent;border:none;}"
            + f"\n#TabPixelArt,#TabPixelWalk{{background:{bg};border:none;}}"
            + build_std_button_qss(theme.is_dark)   # 第十六阶段：标准按钮 kind=std（全站同一份规则）
        )

    def _apply_tab_page_bg(self, widget) -> None:
        name = widget.objectName() or "TabPixel"
        widget.setAttribute(Qt.WA_StyledBackground, True)
        widget.setAutoFillBackground(True)
        bg = tk("panel_2")
        widget.setStyleSheet(f"#{name}{{background:{bg};border:none;}}")

    def _apply_tab_chrome(self) -> None:
        self.setStyleSheet(self._tab_qss())
        for page in (getattr(self, "page_art", None), getattr(self, "page_walk", None)):
            if page is not None:
                self._apply_tab_page_bg(page)
        self._refresh_tabs()

    def _refresh_tabs(self) -> None:
        tabs = getattr(self, "tabs", None)
        if tabs is None:
            return
        idx = tabs.currentIndex()
        tabs.setProperty("firstSelected", "true" if idx == 0 else "false")
        style = tabs.style()
        if style is not None:
            style.unpolish(tabs)
            style.polish(tabs)
        tabs.update()
        bar = tabs.tabBar()
        if bar is None:
            return
        dark = bool(theme.is_dark)
        sel = QColor(tk("text_strong"))
        idle = QColor(tk("text_faint"))
        idle.setAlpha(150 if dark else 160)
        for i in range(tabs.count()):
            bar.setTabTextColor(i, sel if i == idx else idle)
        try:
            bar.setDrawBase(False)
        except Exception:
            pass

    def _build_art(self) -> None:
        lay = self.page_art.layout()
        lay.setSpacing(6)
        tools = QHBoxLayout()
        tools.setSpacing(8)
        self._art_cell_btns = {}
        self._art_color_btns = {}   # 原色/16色/32色 三个选择按钮
        tools.addLayout(self._cell_row("art"))
        tools.addLayout(self._color_row("art"))
        tools.addStretch(1)
        # 第十四阶段：画板工程 .pxb 新建 / 打开 / 保存 / 另存为（与动效页同款），左边显示工程名，未保存带 *
        self.lbl_art_project = QLabel("")
        self.lbl_art_project.setMaximumWidth(160)
        self.lbl_art_project.setMinimumWidth(40)
        tools.addWidget(self.lbl_art_project, 0)
        self._art_project_btns = {}
        proj = QHBoxLayout()
        proj.setSpacing(3)
        for key, icon, tip, slot in (
            ("new", "proj_new", "新建画板工程：四个方向都清空成一个空底层（格子尺寸不变）", self._new_art_project),
            ("open", "proj_open", "打开画板工程（.pxb）；也可把 .pxb 直接拖进画板页", self._open_art_project_dialog),
            ("save", "save", "保存工程（四个方向全部图层、格子、颜色、名称一次存进 .pxb）", self._save_art_project),
            ("save_as", "save_as", "工程另存为（.pxb）", self._save_art_project_as),
        ):
            btn = _IconButton(icon, tip, "small")
            btn.clicked.connect(slot)
            self._art_project_btns[key] = btn
            proj.addWidget(btn, 0)
        tools.addLayout(proj)
        tools.addSpacing(4)
        b = self._medium("批量导入", self._browse_art)
        b.setToolTip("批量导入四向：可多选文件或选一个文件夹，按文件名自然排序，依次填入 正→左→后→右 的当前图层（最多 4 张），一步可撤销")
        self.btn_art_batch = b
        tools.addWidget(b, 0)
        self._art_toolbar = tools
        lay.addLayout(tools)

        mid = QHBoxLayout()
        mid.setSpacing(8)
        self.art_canvas = _PixelCanvas()
        self.art_canvas.setMinimumSize(160, 160)
        self.art_canvas.setToolTip("拖入图片或直接绘制")
        self.art_canvas.dropped.connect(lambda path: self._ingest_art(self._art_dir, path))
        self.art_canvas.press_px.connect(lambda x, y, bg: self._canvas_press(("art", self._art_dir), x, y, bg))
        self.art_canvas.move_px.connect(lambda x, y: self._canvas_move(("art", self._art_dir), x, y))
        self.art_canvas.release_px.connect(lambda x, y: self._canvas_release(("art", self._art_dir), x, y))
        mid.addWidget(self.art_canvas, 1)
        self.art_canvas.project_drop = self._drop_art_project
        self.art_canvas.single_drop = True
        self.art_canvas.multi_refused = self._refuse_art_multi_drop
        mid.addWidget(self._build_layer_panel(), 0)
        lay.addLayout(mid, 1)

        strip = QHBoxLayout()
        strip.setSpacing(8)
        self._dirs = {}
        for direction in self._board_order:
            cell = _DirCell(direction)
            cell.setFixedHeight(84)
            cell.setToolTip(_ART_SPEC)
            cell.clicked.connect(self._select_art)
            cell.dropped.connect(self._ingest_art)
            cell.save_clicked.connect(self._quick_save_art)
            cell.view.project_drop = self._drop_art_project
            cell.view.single_drop = True
            cell.view.multi_refused = self._refuse_art_multi_drop
            cell.view.swap.connect(self._swap_dirs)
            self._dirs[direction] = cell
            strip.addWidget(cell, 1)
        lay.addLayout(strip, 0)

        out = QHBoxLayout()
        out.setSpacing(6)
        self.edit_art_name = self._name_edit(self._art_name, "画板", self._on_art_name)
        self.chk_art_dollar = self._dollar_box(self._art_dollar, self._on_art_dollar)
        out.addWidget(self.edit_art_name, 0)
        out.addWidget(self.chk_art_dollar, 0)
        out.addWidget(self._out_caption(), 0)
        out.addWidget(self._primary("当前", self._save_current_png, "保存当前 PNG：当前方向合成图，原像素  Ctrl+S"), 0)
        out.addWidget(self._primary("整表", self._export_art_sheet, "保存 PNG 整表：四个方向拼一张"), 0)
        out.addWidget(self._primary("四向", self._export_art_dirs, "保存 PNG 四向：每个方向一张"), 0)
        self.lbl_art_status = QLabel("拖图进来或直接画；滚轮缩放，中键拖动；1/2/3/4 切方向（详见缩略图提示）")
        self.lbl_art_status.setWordWrap(False)
        self.lbl_art_status.setMinimumWidth(40)
        self.lbl_art_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        out.addWidget(self.lbl_art_status, 1)
        lay.addLayout(out)
        self._style_one_status(self.lbl_art_status, self._art_tone)
        self.page_art.setAcceptDrops(True)
        self.page_art.installEventFilter(self)

    def _build_layer_panel(self) -> QWidget:
        box = QWidget()
        box.setFixedWidth(184)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(3)
        self.lbl_layers = QLabel("图层")
        self.lbl_layers.setProperty("pixelMuted", "1")
        self.lbl_layers.setToolTip("上层在上、底层在最下；点一行切换，双击改名；PageUp/PageDown 切层")
        head.addWidget(self.lbl_layers, 1)
        self._layer_btns = {
            "up": _IconButton("layer_up", "当前层上移一层（可撤销 Ctrl+Z）", "small"),
            "down": _IconButton("layer_down", "当前层下移一层（可撤销 Ctrl+Z）", "small"),
            "add": _IconButton("add", "加一层（最多 6 层）", "small"),
            "del": _IconButton("delete", "删除此层（底层不能删）", "small"),
        }
        self._layer_btns["up"].clicked.connect(lambda: self._move_layer(1))
        self._layer_btns["down"].clicked.connect(lambda: self._move_layer(-1))
        self._layer_btns["add"].clicked.connect(self._add_layer)
        self._layer_btns["del"].clicked.connect(self._delete_layer)
        for key in ("up", "down", "add", "del"):
            head.addWidget(self._layer_btns[key], 0)
        lay.addLayout(head)
        self.layer_host = QWidget()
        self.layer_rows = QVBoxLayout(self.layer_host)
        self.layer_rows.setContentsMargins(0, 0, 0, 0)
        self.layer_rows.setSpacing(2)
        self._layer_thumbs = {}
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(120)
        self._thumb_timer.timeout.connect(self._update_layer_thumbs)
        lay.addWidget(self.layer_host, 0)
        lay.addStretch(1)
        return box

    def _build_walk(self) -> None:
        """第十阶段：顶栏（格子/颜色/批量导入，同画板页）→ 编辑器 | 预览+播放条 → 时间轴（帧操作·底图·帧条）→ 输出行。"""
        lay = self.page_walk.layout()
        lay.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(8)
        self._walk_cell_btns = {}
        self._walk_color_btns = {}
        top.addLayout(self._cell_row("walk"))
        top.addLayout(self._color_row("walk"))
        top.addStretch(1)
        # 第十三阶段：工程 新建 / 打开 / 保存 / 另存为，左边显示工程名，未保存带 *
        self.lbl_project = QLabel("")
        self.lbl_project.setMaximumWidth(220)
        self.lbl_project.setMinimumWidth(40)
        top.addWidget(self.lbl_project, 0)
        self._project_btns = {}
        for key, icon, tip, slot in (
            ("new", "proj_new", "新建工程：清空动效页的帧和底图（格子尺寸不变）", self._new_project),
            ("open", "proj_open", "打开动效工程（.pxa）；也可把 .pxa 直接拖进动效页", self._open_project_dialog),
            ("save", "save", "保存工程（所有帧、底图、时长、帧率、调色盘一次存进 .pxa）", self._save_project),
            ("save_as", "save_as", "工程另存为（.pxa）", self._save_project_as),
        ):
            btn = _IconButton(icon, tip, "small")
            btn.clicked.connect(slot)
            self._project_btns[key] = btn
            top.addWidget(btn, 0)
        top.addSpacing(4)
        b = self._medium("批量导入", self._browse_walk)
        b.setToolTip("批量导入帧：可多选文件或选一个文件夹，按文件名自然排序，从当前帧起依次填入（最多 64 帧），一步可撤销")
        self.btn_walk_batch = b
        top.addWidget(b, 0)
        lay.addLayout(top)

        mid = QHBoxLayout()
        mid.setSpacing(8)
        self.editor = _PixelCanvas()
        self.editor.setMinimumSize(160, 160)
        self.editor.setToolTip("拖入图片或直接绘制当前帧")
        self.editor.press_px.connect(lambda x, y, bg: self._canvas_press(('frame',), x, y, bg))
        self.editor.move_px.connect(lambda x, y: self._canvas_move(('frame',), x, y))
        self.editor.release_px.connect(lambda x, y: self._canvas_release(('frame',), x, y))
        self.editor.space_tap.connect(lambda: self._run_shortcut(self._toggle_play))
        self.editor.single_drop = True
        self.editor.multi_refused = self._refuse_multi_drop
        self.editor.dropped.connect(lambda path: self._ingest_frame(self._frame_i, path))
        mid.addWidget(self.editor, 1)

        self.walk_side = QWidget()
        side = QVBoxLayout(self.walk_side)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(6)
        self.preview = _SpriteView()
        self.preview.setMinimumSize(160, 160)
        self.preview.setAcceptDrops(False)
        self.preview.set_hint("预览")
        self.preview.setToolTip("播放预览（只显示，不改图）；右上角放大/缩小预览栏")
        self.lbl_preview_info = QLabel("", self.preview)
        self.lbl_preview_info.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.btn_preview_size = _IconButton("expand", "放大预览栏", "corner", parent=self.preview)
        self.btn_preview_size.clicked.connect(self._toggle_preview_size)
        self.preview.installEventFilter(self)
        side.addWidget(self.preview, 1)

        def sep():
            line = QFrame()
            line.setFixedSize(1, 18)
            self._walk_seps.append(line)
            return line

        self._walk_seps = []
        bar1 = QHBoxLayout()
        bar1.setSpacing(4)
        self.btn_play = _IconButton("play", "播放 / 停止  Space（焦点不在输入框；编辑器上单按空格，按住空格拖动仍是平移）", "tool")
        self.btn_play.clicked.connect(self._toggle_play)
        bar1.addWidget(self.btn_play, 0)
        bar1.addSpacing(2)
        self._mode_btns = {}
        for mode, icon, tip in (("loop", "loop", "循环：1→最后一帧→1…"), ("pingpong", "pingpong", "乒乓：1→最后→倒回 1…")):
            btn = _IconButton(icon, tip, "small", checkable=True)
            btn.clicked.connect(lambda _=False, m=mode: self._pick_play_mode(m))
            self._mode_btns[mode] = btn
            bar1.addWidget(btn, 0)
        bar1.addWidget(sep(), 0)
        self.btn_onion = _IconButton(
            "onion", "洋葱皮：前面的帧偏红、后面的帧偏蓝，半透明垫在当前帧下面；只显示，不导出，播放时不显示",
            "small", checkable=True,
        )
        self.btn_onion.clicked.connect(lambda _=False: self._toggle_onion())
        bar1.addWidget(self.btn_onion, 0)
        self._onion_btns = {}
        for n in (1, 2):
            btn = _IconButton(f"pips{n}", f"洋葱皮前后各 {n} 帧", "small", checkable=True)
            btn.clicked.connect(lambda _=False, v=n: self._pick_onion_n(v))
            self._onion_btns[n] = btn
            bar1.addWidget(btn, 0)
        bar1.addStretch(1)
        side.addLayout(bar1)

        bar2 = QHBoxLayout()
        bar2.setSpacing(4)
        self.fps = QSlider(Qt.Horizontal)
        self.fps.setRange(4, 16)
        self.fps.setValue(self._fps)
        self.fps.setMinimumWidth(60)
        self.fps.setFixedHeight(18)
        self.fps.setFocusPolicy(Qt.NoFocus)     # 不抢 ←/→（切帧）
        self.fps.setToolTip("基础帧率 4–16 帧/秒")
        self.fps.valueChanged.connect(self._on_fps)
        self.lbl_fps = QLabel("")
        self.lbl_fps.setFixedWidth(46)
        bar2.addWidget(self.fps, 1)
        bar2.addWidget(self.lbl_fps, 0)
        bar2.addWidget(sep(), 0)
        self._hold_btns = {}
        for n in _HOLDS:
            btn = _IconButton(
                f"hold{n}", f"当前帧时长 ×{n}（停 {n} 倍时间，GIF 也按这个写）；焦点在时间轴上时 +/- 调整", "small", checkable=True,
            )
            btn.clicked.connect(lambda _=False, v=n: self._pick_hold(v))
            self._hold_btns[n] = btn
            bar2.addWidget(btn, 0)
        side.addLayout(bar2)
        self.walk_side.setFixedWidth(_SIDE_W[self._preview_big])
        mid.addWidget(self.walk_side, 0)
        lay.addLayout(mid, 1)

        # 时间轴：左边帧操作 2×3 图标，中间底图格，右边可横向滚动的帧条
        tl = QHBoxLayout()
        tl.setSpacing(8)
        ops = QWidget()
        grid = QGridLayout(ops)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)
        self._frame_op_btns = {}
        for i, (key, icon, tip, slot) in enumerate((
            ("add", "add", "加一帧（空白，放最后）", self._add_frame),
            ("dup", "dup", "复制当前帧（插在后面）", self._dup_frame),
            ("del", "delete", "删除当前帧", self._del_frame),
            ("left", "left", "当前帧往前挪一格  Alt+←", lambda: self._move_frame(-1)),
            ("right", "right", "当前帧往后挪一格  Alt+→", lambda: self._move_frame(1)),
            ("copy_base", "copy_base", "复制底图到此帧：用底图替换当前帧（可撤销）", self._copy_base),
        )):
            btn = _IconButton(icon, tip, "tool")
            btn.clicked.connect(slot)
            self._frame_op_btns[key] = btn
            grid.addWidget(btn, i // 3, i % 3)
        tl.addWidget(ops, 0, Qt.AlignVCenter)

        self.base_view = _PixelCanvas()
        self.base_view.setFixedSize(_FrameCell.SIDE, _FrameCell.SIDE)
        self.base_view._show_info = False
        self.base_view.setToolTip("底图（参考）：拖入图片；可直接在上面改；复制到帧用左边的图标")
        self.base_view.dropped.connect(self._ingest_base)
        self.base_view.single_drop = True
        self.base_view.multi_refused = self._refuse_multi_drop
        self.base_view.press_px.connect(lambda x, y, bg: self._canvas_press(('base',), x, y, bg))
        self.base_view.move_px.connect(lambda x, y: self._canvas_move(('base',), x, y))
        self.base_view.release_px.connect(lambda x, y: self._canvas_release(('base',), x, y))
        self.lbl_base = QLabel("底图", self.base_view)
        self.lbl_base.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.lbl_base.move(3, 3)
        self.btn_save_base = _IconButton("save", "保存底图 PNG（原像素）", "corner", parent=self.base_view)
        self.btn_save_base.move(_FrameCell.SIDE - _ICON_SIZES["corner"] - 2, 2)
        self.btn_save_base.clicked.connect(self._quick_save_base)
        tl.addWidget(self.base_view, 0, Qt.AlignVCenter)
        tl.addWidget(sep(), 0, Qt.AlignVCenter)

        self.frame_host = QWidget()
        self.frame_row = QHBoxLayout(self.frame_host)
        self.frame_row.setContentsMargins(0, 0, 0, 0)
        self.frame_row.setSpacing(6)
        self.frame_scroll = QScrollArea()
        self.frame_scroll.setWidgetResizable(True)
        self.frame_scroll.setFrameShape(QScrollArea.NoFrame)
        self.frame_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.frame_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.frame_scroll.setFixedHeight(_FrameCell.SIDE + 12)
        self.frame_scroll.setFocusPolicy(Qt.StrongFocus)
        self.frame_scroll.setToolTip(_TIMELINE_TIP)
        self.frame_scroll.setWidget(self.frame_host)
        self.frame_scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollBar:horizontal{height:6px;background:transparent;margin:0px;}"
            "QScrollBar::handle:horizontal{background:rgba(128,128,128,110);border-radius:3px;min-width:24px;}"
            "QScrollBar::add-line,QScrollBar::sub-line{width:0px;}"
        )
        self.frame_host.setStyleSheet("background:transparent;")
        self._frame_cells = []
        self._frame_sig = None
        tl.addWidget(self.frame_scroll, 1)
        lay.addLayout(tl, 0)

        out = QHBoxLayout()
        out.setSpacing(6)
        self.edit_walk_name = self._name_edit(self._walk_name, "角色", self._on_walk_name)
        self.chk_walk_dollar = self._dollar_box(self._walk_dollar, self._on_walk_dollar)
        out.addWidget(self.edit_walk_name, 0)
        out.addWidget(self.chk_walk_dollar, 0)
        out.addWidget(self._out_caption(), 0)
        out.addWidget(self._primary("当前", self._save_current_png, "保存当前 PNG：当前帧（或底图），原像素  Ctrl+S"), 0)
        out.addWidget(self._primary("整表", self._export_walk_sheet, "保存 PNG 整表：所有帧拼一张"), 0)
        out.addWidget(self._primary("逐帧", self._export_frames, "保存 PNG 逐帧：每帧一张"), 0)
        self.btn_gif = self._primary("GIF", self._export_gif, "保存 GIF 动图：按基础帧率 × 每帧时长、循环/乒乓顺序写")
        out.addWidget(self.btn_gif, 0)
        self.lbl_walk_status = QLabel("拖图到左下底图格，复制到帧上再画；更多操作见时间轴提示")
        self.lbl_walk_status.setWordWrap(False)
        self.lbl_walk_status.setMinimumWidth(40)
        self.lbl_walk_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        out.addWidget(self.lbl_walk_status, 1)
        lay.addLayout(out)
        self._style_one_status(self.lbl_walk_status, self._walk_tone)
        self._style_walk_bits()
        # 第十三阶段：.pxa 拖到动效页任何地方都能打开
        for view in (self.editor, self.base_view):
            view.project_drop = self._drop_project
        self.page_walk.setAcceptDrops(True)
        self.page_walk.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is getattr(self, "preview", None) and event.type() in (QEvent.Resize, QEvent.Show):
            self._place_preview_overlay()
        if obj is getattr(self, "page_walk", None) and event.type() in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop):
            md = event.mimeData()
            if md is not None and md.hasUrls() and len(md.urls()) > 1:
                event.ignore()
                if event.type() == QEvent.DragEnter:
                    self._refuse_multi_drop()
                return True
            path = _pxa_path(md)
            if not path:
                event.ignore()
                return True
            event.acceptProposedAction()
            if event.type() == QEvent.Drop:
                self._drop_project(path)
            return True
        if obj is getattr(self, "page_art", None) and event.type() in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop):
            md = event.mimeData()
            if md is not None and md.hasUrls() and len(md.urls()) > 1:
                event.ignore()
                if event.type() == QEvent.DragEnter:
                    self._refuse_art_multi_drop()
                return True
            path = _pxa_path(md)     # 只管工程文件；图片仍由画布/缩略图按原规则处理
            if not path:
                return super().eventFilter(obj, event)
            event.acceptProposedAction()
            if event.type() == QEvent.Drop:
                self._drop_art_project(path)
            return True
        return super().eventFilter(obj, event)

    def _place_preview_overlay(self) -> None:
        pv = self.preview
        self.lbl_preview_info.adjustSize()
        self.lbl_preview_info.move(4, 4)
        self.btn_preview_size.move(pv.width() - self.btn_preview_size.width() - 4, 4)
        self.lbl_preview_info.raise_()
        self.btn_preview_size.raise_()

    def _toggle_preview_size(self) -> None:
        self._preview_big = not self._preview_big
        self._apply_preview_size()
        self._touch()

    def _apply_preview_size(self) -> None:
        self.walk_side.setFixedWidth(_SIDE_W[self._preview_big])
        self.btn_preview_size.set_icon("shrink" if self._preview_big else "expand")
        self.btn_preview_size.setToolTip("缩小预览栏" if self._preview_big else "放大预览栏")
        self._place_preview_overlay()

    def _seq_fps(self) -> float:
        """整段实际平均帧率：播放序列帧数 ÷ 总时长（含每帧时长倍数）。"""
        seq = self._play_sequence()
        holds = self._holds()
        total = sum(self._interval() * holds[i] for i in seq)
        return (len(seq) * 1000.0 / total) if total else float(self._fps)

    def _update_preview_info(self, shown: int = None) -> None:
        lab = getattr(self, "lbl_preview_info", None)
        if lab is None:
            return
        n = len(self._frames)
        i = self._frame_i if shown is None else shown
        playing = self._timer.isActive()
        lab.setText(f"{'▶ ' if playing else ''}{i + 1:02d}/{n:02d} · {self._seq_fps():.1f}帧/秒")
        lab.setToolTip("")
        self._place_preview_overlay()

    def _build_palette(self) -> QWidget:
        """第九阶段：底部按功能分组横排——工具 | 画笔 | 视图 | 颜色 | 色板(预设+色块)。"""
        box = QWidget()
        box.setObjectName("PixelPalette")
        box.setAttribute(Qt.WA_StyledBackground, True)
        box.setAutoFillBackground(True)
        box.setFixedHeight(146)   # 原 150：分组后略矮，色块仍 26px
        lay = QHBoxLayout(box)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(7)
        self._palette_seps = []
        self._palette_groups = {}

        def group(key, title, inner):
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(3)
            lab = QLabel(title)
            lab.setProperty("pixelMuted", "1")
            v.addWidget(lab, 0)
            if isinstance(inner, QWidget):
                v.addWidget(inner, 0)
            else:
                v.addLayout(inner)
            v.addStretch(1)
            w.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
            self._palette_groups[key] = w
            if self._palette_seps or len(self._palette_groups) > 1:
                sep = QFrame()
                sep.setFixedWidth(1)
                self._palette_seps.append(sep)
                lay.addWidget(sep, 0)
            lay.addWidget(w, 0)
            return w

        grid = QGridLayout()
        grid.setSpacing(3)
        self._tool_btns = {}
        for i, (key, text) in enumerate(_TOOLS):
            btn = _IconButton(key, f"{text} {_TOOL_KEYS.get(key, '')}", "tool", True)
            btn.clicked.connect(lambda _=False, k=key: self._set_tool(k))
            self._tool_btns[key] = btn
            grid.addWidget(btn, i // 3, i % 3)
        self._mark_choice(self._tool_btns, self._tool)
        group("tools", "工具", grid)

        brush, view = self._build_tool_options()
        group("brush", "画笔", brush)
        group("view", "视图 / 编辑", view)

        col = QGridLayout()
        col.setSpacing(3)
        self.well_fg = _ColorWell()
        self.well_bg = _ColorWell()
        self.well_fg.setToolTip("前景色（左键画）")
        self.well_bg.setToolTip("背景色（右键画）")
        wells = QHBoxLayout()
        wells.setSpacing(4)
        wells.addWidget(self.well_fg, 0)
        wells.addWidget(self.well_bg, 0)
        wells.addStretch(1)
        col.addLayout(wells, 0, 0, 1, 3)
        self.btn_swap = _IconButton("swap", "对调前景/背景色", "tool")
        self.btn_swap.clicked.connect(self._swap_colors)
        self.btn_pick_screen = _IconButton(
            "picker", "吸管（全屏取色）K：屏幕上任意位置都能取，包括本程序画布。左键前景，右键或 Shift+左键背景，Esc 取消", "tool",
        )
        self.btn_pick_screen.clicked.connect(self._start_screen_pick)
        self.btn_store_color = _IconButton("store", "存入：把前景色存进下一个空的自定义色块；Ctrl+点自定义色块清除", "tool")
        self.btn_store_color.clicked.connect(self._store_fg_custom)
        col.addWidget(self.btn_swap, 1, 0)
        col.addWidget(self.btn_pick_screen, 1, 1)
        col.addWidget(self.btn_store_color, 1, 2)
        self.lbl_hex = QLabel("")
        self.lbl_hex.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        col.addWidget(self.lbl_hex, 2, 0, 1, 3)
        group("color", "颜色", col)

        self._preset_btns = {}
        row = self._choice_row(
            "", self._palette_names(), self._palette_name, self._preset_btns, self._pick_palette, compact=True,
        )
        presets = QVBoxLayout()
        presets.setSpacing(3)
        while row.count():
            item = row.takeAt(0)
            if item.widget() is not None:
                presets.addWidget(item.widget(), 0)
        for name, btn in self._preset_btns.items():
            btn.setToolTip(f"色板预设：{name}")
        group("preset", "色板", presets)

        self.swatches = _SwatchGrid()
        self.swatches.picked.connect(self._pick_swatch)
        self.swatches.store_custom.connect(self._store_custom)
        self.swatches.clear_custom.connect(self._clear_custom)
        lay.addWidget(self.swatches, 1)
        return box

    def _out_caption(self) -> QLabel:
        lab = QLabel("保存")
        lab.setProperty("pixelMuted", "1")
        return lab

    def _medium(self, text, slot) -> QPushButton:
        btn = apply_std_button(QPushButton(text))   # 第十六阶段：全站标准按钮 kind=std
        btn.setFocusPolicy(Qt.NoFocus)
        btn.clicked.connect(slot)
        return btn

    def _primary(self, text, slot, tip="") -> QPushButton:
        btn = self._medium(text, slot)
        btn.setProperty("pixelPrimary", "1")
        if tip:
            btn.setToolTip(tip)
        if not hasattr(self, "_primary_btns"):
            self._primary_btns = []
        self._primary_btns.append(btn)
        btn.setStyleSheet(self._qss_choice(True))
        return btn

    def _save_current_png(self) -> None:
        if self._on_walk_tab():
            if self._walk_focus == "base":
                self._quick_save_base()
                return
            frames = self._frames
            if 0 <= self._frame_i < len(frames):
                self._quick_png(f"{self._walk_stem()}_{self._frame_i + 1:02d}", frames[self._frame_i], self._set_walk_status)
            return
        d = self._art_dir
        self._quick_png(f"{self._art_stem()}_{DIR_LABEL[d]}", self._composite(d), self._set_art_status)

    def _name_edit(self, text, placeholder, slot) -> QLineEdit:
        edit = QLineEdit()
        edit.setObjectName("CalcInput")
        edit.setText(text)
        edit.setPlaceholderText(placeholder)
        edit.setFixedWidth(84)
        edit.setToolTip("导出文件名前缀")
        edit.editingFinished.connect(slot)
        return edit

    def _dollar_box(self, checked, slot) -> QCheckBox:
        box = QCheckBox("$")
        box.setToolTip("文件名加 $")
        box.setChecked(checked)
        box.stateChanged.connect(slot)
        return box

    def _choice_row(self, title, items, current, store, slot, compact=False) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        if title:
            lab = QLabel(title)
            lab.setFixedWidth(max(36, lab.fontMetrics().horizontalAdvance(title) + 6))
            lab.setProperty("pixelMuted", "1")
            row.addWidget(lab, 0)
        for item in items:
            if isinstance(item, tuple):
                value, text = item
            else:
                value, text = item, str(item)
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setFixedHeight(_BTN_H)
            btn.clicked.connect(lambda _=False, v=value: slot(v))
            store[value] = btn
            row.addWidget(btn, 0 if compact else 1)
        self._mark_choice(store, current)
        return row

    def _mark_choice(self, store, current) -> None:
        for value, btn in store.items():
            btn.setChecked(value == current)
        custom = store.get("custom")
        if custom is not None:
            is_custom = current not in store
            custom.setChecked(is_custom)
            custom.setText(f"自定义 {current}" if is_custom else "自定义…")
            custom.setToolTip("自定义正方形边长（8–512 像素），大图/战斗图用")
        self._style_choices()

    def _cell_row(self, side: str) -> QHBoxLayout:
        """格子：16/24/32 预设 + 宽×高 两个输入框（8–512，可不等）。改完回车/离开即生效，敲数字时防抖。"""
        store = self._art_cell_btns if side == "art" else self._walk_cell_btns
        pick = self._pick_art_cell if side == "art" else self._pick_walk_cell
        cur = self._art_cell if side == "art" else self._walk_cell
        row = self._choice_row("格子", list(_CELL_PRESETS), None, store, lambda v: pick((v, v)), compact=True)
        for value, btn in store.items():
            btn.setToolTip(f"{value}×{value} 像素")
        spins = []
        for i, tip in enumerate(("宽（像素）", "高（像素）")):
            sp = QSpinBox()
            sp.setRange(CELL_MIN, CELL_MAX)
            sp.setValue(cur[i])
            sp.setFixedHeight(_BTN_H)
            sp.setFixedWidth(52)
            sp.setButtonSymbols(QAbstractSpinBox.NoButtons)
            sp.setAlignment(Qt.AlignCenter)
            sp.setKeyboardTracking(False)
            sp.setToolTip(f"自定义{tip}，{CELL_MIN}–{CELL_MAX}；回车或离开输入框生效，可宽高不等")
            spins.append(sp)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(400)
        apply = lambda: (timer.stop(), pick((spins[0].value(), spins[1].value())))
        timer.timeout.connect(apply)
        for sp in spins:
            sp.valueChanged.connect(lambda _v: timer.start())
            sp.installEventFilter(self._wheel_guard)
            sp.editingFinished.connect(apply)
        row.addSpacing(4)
        row.addWidget(spins[0], 0)
        x = QLabel("×")
        x.setProperty("pixelMuted", "1")
        row.addWidget(x, 0)
        row.addWidget(spins[1], 0)
        if not hasattr(self, "_cell_spins"):
            self._cell_spins = {}
        self._cell_spins[side] = spins
        self._mark_cell(side)
        return row

    def _mark_cell(self, side: str) -> None:
        """宽=高且是预设时高亮那个预设；输入框始终显示当前宽×高。"""
        store = self._art_cell_btns if side == "art" else self._walk_cell_btns
        w, h = self._art_cell if side == "art" else self._walk_cell
        for value, btn in store.items():
            btn.setChecked(w == h == value)
        for sp, v in zip(getattr(self, "_cell_spins", {}).get(side, []), (w, h)):
            if sp.value() != v:
                sp.blockSignals(True)
                sp.setValue(v)
                sp.blockSignals(False)
        self._style_choices()

    def _color_row(self, side: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        lab = QLabel("颜色")
        lab.setProperty("pixelMuted", "1")
        row.addWidget(lab, 0)
        store = self._art_color_btns if side == "art" else self._walk_color_btns
        store.clear()
        slot = self._pick_art_colors if side == "art" else self._pick_walk_colors
        for value, text in _COLOR_ITEMS:
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setFixedHeight(_BTN_H)
            btn.setToolTip("原色＝不减色" if not value else f"把图减到 {value} 种颜色（导入和切换时生效，可撤销）")
            btn.clicked.connect(lambda _=False, v=value, f=slot: f(v))
            store[value] = btn
            row.addWidget(btn, 0)
        self._mark_colors(side)
        return row

    def _mark_colors(self, side: str) -> None:
        store = getattr(self, "_art_color_btns" if side == "art" else "_walk_color_btns", None)
        if not store:
            return
        value = self._art_colors if side == "art" else self._walk_colors
        self._mark_choice(store, value)

    def _qss_field(self) -> str:
        return (
            f"QComboBox,QSpinBox{{background:{tk('panel_2')};color:{tk('text')};"
            f"border:1px solid {tk('border')};border-radius:6px;{_BTN_QSS}}}"
            f"QSpinBox{{color:{tk('text_strong')};selection-background-color:{tk('accent')};"
            f"selection-color:#ffffff;}}"
            f"QSpinBox:disabled,QComboBox:disabled{{color:{tk('text_mut')};}}"
            f"QComboBox:hover,QSpinBox:hover,QSpinBox:focus{{border-color:{tk('accent')};}}"
            f"QComboBox::drop-down{{border:none;width:14px;}}"
            f"QComboBox QAbstractItemView{{background:{tk('panel')};color:{tk('text')};"
            f"border:1px solid {tk('border')};selection-background-color:{tk('accent')};"
            f"selection-color:{tk('text_strong')};font-size:12px;outline:none;}}"
        )

    def _qss_choice(self, on: bool) -> str:
        if on:
            return (
                f"QPushButton{{background:{tk('accent')};color:#ffffff;border:1px solid {tk('accent')};"
                f"border-radius:6px;{_BTN_QSS}}}"
            )
        return (
            f"QPushButton{{background:transparent;color:{tk('text')};border:1px solid {tk('border')};"
            f"border-radius:6px;{_BTN_QSS}}}"
            f"QPushButton:hover{{border-color:{tk('accent')};}}"
        )

    def _style_choices(self) -> None:
        stores = (
            getattr(self, "_art_cell_btns", {}),
            getattr(self, "_art_color_btns", {}),
            getattr(self, "_walk_cell_btns", {}),
            getattr(self, "_walk_color_btns", {}),
            getattr(self, "_tool_btns", {}),
            getattr(self, "_preset_btns", {}),
            getattr(self, "_brush_btns", {}),
            getattr(self, "_toggle_btns", {}),
            getattr(self, "_mode_btns", {}),
            getattr(self, "_onion_btns", {}),
            getattr(self, "_hold_btns", {}),
            {"a": self.btn_onion} if getattr(self, "btn_onion", None) else {},
        )
        for store in stores:
            for btn in store.values():
                if isinstance(btn, _IconButton):
                    btn.update()
                else:
                    btn.setStyleSheet(self._qss_choice(btn.isChecked()))
        field = self._qss_field()
        for w in [
            sp for pair in getattr(self, "_cell_spins", {}).values() for sp in pair
        ]:
            w.setStyleSheet(field)
        for btn in self.findChildren(_IconButton):
            btn.update()
        for name in ("chk_art_dollar", "chk_walk_dollar"):
            chk = getattr(self, name, None)
            if chk is not None:
                chk.setStyleSheet(f"QCheckBox{{color:{tk('text')};background:transparent;font-size:13px;font-weight:600;}}")
        for sep in getattr(self, "_palette_seps", []):
            sep.setStyleSheet(f"background:{tk('border')};border:none;")
        for lab in self.findChildren(QLabel):
            if str(lab.property("pixelMuted") or "") == "1":
                lab.setStyleSheet(f"color:{tk('text_mut')};background:transparent;font-size:12px;")
        fps = getattr(self, "lbl_fps", None)
        if fps is not None:
            fps.setStyleSheet(f"color:{tk('text')};background:transparent;font-size:12px;")
        hex_lbl = getattr(self, "lbl_hex", None)
        if hex_lbl is not None:
            hex_lbl.setStyleSheet(f"color:{tk('text')};background:transparent;font-size:12px;")
        for btn in getattr(self, "_primary_btns", []):
            btn.setStyleSheet(self._qss_choice(True))
        self._unify_buttons()

    def _unify_buttons(self) -> None:
        """本页所有按钮同一套内边距和高度；只动本页，不碰全局主题。"""
        plain = "QPushButton{" + _BTN_QSS + "}"
        for btn in self.findChildren(QPushButton):
            if isinstance(btn, _IconButton):   # 例外：图标按钮，尺寸见 _ICON_SIZES
                continue
            if btn.property("kind") == "std":   # 第十六阶段：标准按钮的高度/内边距由全局 kind=std 规则管
                continue
            if btn.height() != _BTN_H or btn.minimumHeight() != _BTN_H:
                btn.setFixedHeight(_BTN_H)
            if _BTN_QSS not in btn.styleSheet():
                btn.setStyleSheet(plain)

    def _style_one_status(self, label, tone: str) -> None:
        color = {"ok": tk("ok"), "err": tk("err"), "warn": tk("warn")}.get(tone, tk("text_mut"))
        label.setStyleSheet(f"color:{color};background:transparent;font-size:12px;")

    def _set_art_status(self, text: str, tone: str = "mut") -> None:
        self._art_tone = tone
        self.lbl_art_status.setText(text)
        self._style_one_status(self.lbl_art_status, tone)

    def _set_walk_status(self, text: str, tone: str = "mut") -> None:
        self._walk_tone = tone
        self.lbl_walk_status.setText(text)
        self._style_one_status(self.lbl_walk_status, tone)

    def refresh_theme(self, *_args) -> None:
        self._apply_tab_chrome()
        self._apply_palette_chrome()
        self._layer_sig_cache = None
        for cell in self._dirs.values():
            cell.restyle()
        for cell in self._frame_cells:
            cell.restyle()
        self._style_walk_bits()
        for view in (self.art_canvas, self.base_view, self.editor, self.preview, self.well_fg, self.well_bg, self.swatches):
            view.update()
        self._style_choices()
        self._sync_art()
        self._sync_walk()
        self._sync_palette()
        self._style_one_status(self.lbl_art_status, self._art_tone)
        self._style_one_status(self.lbl_walk_status, self._walk_tone)

    def _style_walk_bits(self) -> None:
        tag = f"color:{tk('text')};background:{tk('panel')};border:1px solid {tk('border')};border-radius:3px;padding:0px 4px;font-size:11px;"
        for lab in (getattr(self, "lbl_base", None), getattr(self, "lbl_preview_info", None)):
            if lab is not None:
                lab.setStyleSheet(tag)
                lab.adjustSize()
        for line in getattr(self, "_walk_seps", []):
            line.setStyleSheet(f"background:{tk('border')};border:none;")
        if getattr(self, "lbl_fps", None) is not None:
            self.lbl_fps.setStyleSheet(f"color:{tk('text_mut')};font-size:12px;")
        for lab in (getattr(self, "lbl_project", None), getattr(self, "lbl_art_project", None)):
            if lab is not None:
                lab.setStyleSheet(f"color:{tk('text')};background:transparent;font-size:12px;")

    def _select_art(self, direction: str) -> None:
        if direction not in DIRS:
            return
        if direction != self._art_dir:
            self._commit_selection()
        self._art_dir = direction
        self._layer_sig_cache = None
        self._sync_art()

    def _refresh_art_dir(self, direction: str) -> None:
        self._walk_changed()
        """某方向改了：同步缩略图；是当前方向就刷大画布。"""
        comp = self._composite(direction)
        cell = self._dirs.get(direction)
        if cell is not None:
            self._show_image(cell.view, comp)
        if direction == self._art_dir:
            self._show_image(self.art_canvas, comp)
            self._schedule_layer_thumbs()

    def _step_layer(self, delta: int) -> None:
        layers = self._layers[self._art_dir]
        index = self._active_layer.get(self._art_dir, 0) + delta
        if 0 <= index < len(layers):
            self._select_layer(index)
            self._set_art_status(f"当前层：{layers[index]['name']}", "mut")

    def _set_tool(self, name: str) -> None:
        keys = [key for key, _text in _TOOLS]
        if name not in keys:
            name = "pencil"
        if name != getattr(self, "_tool", None):
            self._commit_selection()
        self._tool = name
        drag = name in ("pencil", "eraser")
        for view in (getattr(self, "art_canvas", None), getattr(self, "base_view", None), getattr(self, "editor", None)):
            if view is not None:
                view.set_drag_paint(drag)
        btns = getattr(self, "_tool_btns", None)
        if btns:
            self._mark_choice(btns, name)

    def _apply_palette_chrome(self) -> None:
        palette = getattr(self, "palette", None)
        if palette is None:
            return
        palette.setStyleSheet(
            f"#PixelPalette{{background:{tk('panel')};border:1px solid {tk('border')};border-radius:8px;}}"
        )

    def _hex_text(self, rgba) -> str:
        parts = list(rgba) + [255, 255, 255, 255]
        r, g, b, a = [int(parts[i]) for i in range(4)]
        text = f"#{r:02X}{g:02X}{b:02X}"
        if a < 255:
            text += f" {int(round(a * 100 / 255))}%"
        return text

    def _sync_palette(self) -> None:
        colors = [hex_rgb(item) for item in palette_64(self._palette_name)]
        self.swatches.set_colors(colors, self._fg, self._bg, self._custom_colors)
        self.well_fg.set_rgba(self._fg)
        self.well_bg.set_rgba(self._bg)
        self.lbl_hex.setText(self._hex_text(self._fg))
        self._mark_choice(self._preset_btns, self._palette_name)

    def _pick_swatch(self, rgb, is_bg: bool) -> None:
        color = (int(rgb[0]), int(rgb[1]), int(rgb[2]), 255)
        if is_bg:
            self._bg = color
        else:
            self._fg = color
        self._sync_palette()

    def _swap_colors(self) -> None:
        self._fg, self._bg = self._bg, self._fg
        self._sync_palette()

    def _palette_names(self) -> list:
        return ["默认64"] + list(PALETTES.keys())

    def _store_custom(self, index: int) -> None:
        if 0 <= index < _CUSTOM_SLOTS:
            self._custom_colors[index] = tuple(self._fg[:3])
            self._sync_palette()
            self._touch()

    def _clear_custom(self, index: int) -> None:
        if 0 <= index < _CUSTOM_SLOTS:
            self._custom_colors[index] = None
            self._sync_palette()
            self._touch()

    def _store_fg_custom(self) -> None:
        rgb = tuple(self._fg[:3])
        if rgb in self._custom_colors:
            return
        try:
            index = self._custom_colors.index(None)
        except ValueError:
            self._custom_colors = self._custom_colors[1:] + [None]
            index = _CUSTOM_SLOTS - 1
        self._store_custom(index)

    def _start_screen_pick(self) -> None:
        if self._picker is not None and self._picker.windows:
            return
        self._picker = _ScreenPicker(self._screen_grabber, self)
        self._picker.picked.connect(self._on_screen_pick)
        if not self._picker.start():
            self._tell_for(("art",) if not self._on_walk_tab() else ("frame",))("截不到屏幕", "err")

    def _on_screen_pick(self, rgb, is_bg: bool) -> None:
        self._pick_swatch(rgb, is_bg)
        tell = self._set_walk_status if self._on_walk_tab() else self._set_art_status
        r, g, b = [int(v) for v in rgb[:3]]
        tell(f"已取{'背景' if is_bg else '前景'}色 #{r:02X}{g:02X}{b:02X}", "ok")

    def _pick_palette(self, name: str) -> None:
        if name not in self._palette_names():
            return
        self._palette_name = name
        self._sync_palette()

    def _apply_tool(self, im, x: int, y: int, is_bg: bool, sample=None) -> bool:
        if self._tool == "eraser":
            color = (0, 0, 0, 0)
        else:
            color = self._bg if is_bg else self._fg
        color = tuple(int(c) for c in color)
        if len(color) == 3:
            color = color + (255,)
        else:
            color = color[:4]
        if self._tool == "fill":
            flood_fill(im, x, y, color)
            return True
        if x < 0 or y < 0 or x >= im.size[0] or y >= im.size[1]:
            return False
        im.putpixel((x, y), color)
        return True

    # ================= 共用工具栏（画板、动效都用，放在调色盘区） =================

    def _build_tool_options(self):
        """返回 (画笔组, 视图组) 两个网格：实心/对称/粗细；撤销/重做/适应/网格 + 整图平移。"""
        brush = QGridLayout()
        brush.setSpacing(3)
        view = QGridLayout()
        view.setSpacing(3)
        self._toggle_btns = {}
        self._brush_btns = {}
        self._plain_btns = []

        def make(icon, tip, slot, checkable=False):
            btn = _IconButton(icon, tip, "tool", checkable)
            btn.clicked.connect(lambda _=False: slot())
            return btn

        self._toggle_btns["fill"] = make("shape_fill", "实心：矩形、椭圆画成实心；不按下是空心", self._toggle_shape_fill, True)
        self._toggle_btns["mirror"] = make("mirror", "对称：以画布竖中线为轴左右同时落笔", self._toggle_mirror_draw, True)
        brush.addWidget(self._toggle_btns["fill"], 0, 0)
        brush.addWidget(self._toggle_btns["mirror"], 0, 1)
        for n in (1, 2, 3):
            self._brush_btns[n] = make(f"brush{n}", f"粗细 {n}：画笔 {n}×{n} 像素", lambda v=n: self._set_brush(v), True)
            brush.addWidget(self._brush_btns[n], 1, n - 1)
        undo = make("undo", "撤销  Ctrl+Z", self._undo_action)
        redo = make("redo", "重做  Ctrl+Y / Ctrl+Shift+Z", self._redo_action)
        fit = make("fit", "适应：画布适应窗口  Ctrl+0 或中键双击；滚轮 / [ ] 缩放，中键或空格+左键拖动平移", self._fit_active)
        self._toggle_btns["grid"] = make("grid", "网格：像素网格（放大到 6 倍以上才显示）  Ctrl+G", self._toggle_grid, True)
        for c, btn in enumerate((undo, redo, fit, self._toggle_btns["grid"])):
            view.addWidget(btn, 0, c)
        self._plain_btns += [undo, redo, fit]
        tip = "整图循环平移 1 像素（{}）：移出去的一边从另一边回来  Shift+方向键"
        for c, (icon, word, dx, dy) in enumerate((("left", "左", -1, 0), ("right", "右", 1, 0), ("up", "上", 0, -1), ("down", "下", 0, 1))):
            btn = make(icon, tip.format(word), lambda a=dx, b=dy: self._shift_target(a, b))
            self._plain_btns.append(btn)
            view.addWidget(btn, 1, c)
        self._toggle_btns["shift_all"] = make(
            "shift_all", "应用到全部帧（仅动效页）：按下后整图平移 / Shift+方向键 同时平移所有帧，一步撤销", self._toggle_shift_all, True)
        view.addWidget(self._toggle_btns["shift_all"], 2, 0)
        return brush, view

    def _toggle_shift_all(self) -> None:
        self._walk_shift_all = not self._walk_shift_all
        self._sync_toolbar()
        self._touch()

    def _sync_toolbar(self) -> None:
        btns = getattr(self, "_toggle_btns", None)
        if not btns:
            return
        btns["fill"].setChecked(self._shape_fill)
        btns["mirror"].setChecked(self._mirror_draw)
        btns["grid"].setChecked(self._grid_on)
        if "shift_all" in btns:
            btns["shift_all"].setChecked(bool(getattr(self, "_walk_shift_all", False)))
        for n, btn in self._brush_btns.items():
            btn.setChecked(n == self._brush)
        for view in self._all_canvases():
            view.set_grid(self._grid_on)
        self._style_choices()

    def _toggle_shape_fill(self) -> None:
        self._shape_fill = not self._shape_fill
        self._sync_toolbar()
        self._touch()

    def _toggle_mirror_draw(self) -> None:
        self._mirror_draw = not self._mirror_draw
        self._sync_toolbar()
        self._touch()

    def _toggle_grid(self) -> None:
        self._grid_on = not self._grid_on
        self._sync_toolbar()
        self._touch()

    def _set_brush(self, n: int) -> None:
        self._brush = max(1, min(3, int(n)))
        self._sync_toolbar()
        self._touch()

    def _all_canvases(self) -> list:
        out = []
        for name in ("art_canvas", "base_view", "editor"):
            view = getattr(self, name, None)
            if view is not None:
                out.append(view)
        return out

    def _on_walk_tab(self) -> bool:
        tabs = getattr(self, "tabs", None)
        return tabs is not None and tabs.currentIndex() == 1

    def _active_canvas(self):
        if self._on_walk_tab():
            return self.base_view if self._walk_focus == "base" else self.editor
        return self.art_canvas

    def _zoom_active(self, step: int) -> None:
        if self._on_walk_tab() and self._timeline_focused():
            # 焦点在时间轴：+/- 调当前帧时长，不缩放
            cur = self._holds()[self._frame_i]
            new = max(_HOLDS[0], min(_HOLDS[-1], cur + step))
            if new != cur:
                self._pick_hold(new)
            self._set_walk_status(f"{self._frame_i + 1:02d} 帧时长 ×{new}", "mut")
            return
        self._active_canvas().zoom_by(step)

    def _fit_active(self) -> None:
        self._active_canvas().fit_view()

    def _on_tab_changed(self, _index: int) -> None:
        self._commit_selection()
        self._refresh_tabs()
        self._sync_walk_shortcuts()

    def _sync_walk_shortcuts(self) -> None:
        """←/→/Space 只在动效页启用，画板页不吞方向键和空格。"""
        on = self._on_walk_tab()
        for sc in getattr(self, "_walk_shortcuts", []):
            sc.setEnabled(on)

    # ---------- 快捷键：只在本页有焦点、且焦点不在输入框时生效 ----------

    def _install_shortcuts(self) -> None:
        self._shortcuts = []

        def add(seq, fn):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.setAutoRepeat(seq in ("[", "]", "=", "+", "-", "Left", "Right") or seq.startswith("Shift+"))
            sc.activated.connect(lambda f=fn: self._run_shortcut(f))
            self._shortcuts.append(sc)
            return sc

        for key, letter in _TOOL_KEYS.items():
            add(letter, lambda k=key: self._set_tool(k))
        add("Ctrl+Z", self._undo_action)
        add("Ctrl+Y", self._redo_action)
        add("Ctrl+Shift+Z", self._redo_action)
        add("Ctrl+G", self._toggle_grid)
        add("Ctrl+0", self._fit_active)
        add("Ctrl+S", self._save_current_png)
        add("K", self._start_screen_pick)
        for seq, step in (("[", -1), ("-", -1), ("]", 1), ("=", 1), ("+", 1)):
            add(seq, lambda s=step: self._zoom_active(s))
        for num, d in zip(("1", "2", "3", "4"), self._board_order):
            add(num, lambda v=d: self._shortcut_dir(v))
        add("PgUp", lambda: (not self._on_walk_tab()) and self._step_layer(1))
        add("PgDown", lambda: (not self._on_walk_tab()) and self._step_layer(-1))
        add("Alt+Left", lambda: self._shortcut_move_frame(-1))
        add("Alt+Right", lambda: self._shortcut_move_frame(1))
        for seq, dx, dy in (("Shift+Left", -1, 0), ("Shift+Right", 1, 0), ("Shift+Up", 0, -1), ("Shift+Down", 0, 1)):
            add(seq, lambda a=dx, b=dy: self._shift_target(a, b))
        # 选区专用：只有选区存在时才启用，平时不吞 Enter / Esc
        self._sel_shortcuts = [
            add("Return", self._commit_selection),
            add("Enter", self._commit_selection),
            add("Escape", self._cancel_selection),
        ]
        self._walk_shortcuts = [
            add("Left", lambda: self._step_frame(-1)),
            add("Right", lambda: self._step_frame(1)),
            add("Space", lambda: self._on_walk_tab() and self._toggle_play()),
        ]
        self._sync_sel_shortcuts()
        self._sync_walk_shortcuts()

    def _shortcut_dir(self, direction: str) -> None:
        if self._on_walk_tab():
            return
        self._select_art(direction)
        self._set_art_status(f"当前方向：{DIR_LABEL[direction]}", "mut")

    def _shortcut_move_frame(self, delta: int) -> None:
        if self._on_walk_tab():
            self._move_frame(delta)

    def _sync_sel_shortcuts(self) -> None:
        on = self._sel is not None
        for sc in getattr(self, "_sel_shortcuts", []):
            sc.setEnabled(on)

    def _run_shortcut(self, fn) -> None:
        if not self.isVisible():
            return
        if isinstance(QApplication.focusWidget(), (QLineEdit, QAbstractSpinBox)):
            return
        fn()

    # ---------- 绘制目标与撤销栈 ----------
    # 目标键：("art", 方向, 图层) / ("base",) / ("frame", 帧)

    def _current_key(self):
        if self._on_walk_tab():
            if self._walk_focus == "base":
                return ("base",)
            return ("frame", self._frame_i)
        d = self._art_dir
        return ("art", d, self._active_layer.get(d, 0))

    def _get_img(self, key):
        try:
            if key[0] == "art":
                return self._layers[key[1]][key[2]]["image"]
            if key[0] == "base":
                return self._base_im
            if key[0] == "frame":
                return self._frames[key[1]]
        except (KeyError, IndexError):
            return None
        return None

    def _view_for(self, key):
        if key[0] == "art":
            return self.art_canvas
        if key[0] == "base":
            return self.base_view
        return self.editor

    def _refresh_key(self, key) -> None:
        self._walk_changed()
        if key[0] == "art":
            self._refresh_art_dir(key[1])
            return
        if key[0] == "base":
            self._show_image(self.base_view, self._base_im)
            return
        im = self._get_img(key)
        if im is None:
            return
        if key[1] == self._frame_i:
            self._show_image(self.editor, im)
            if not self._timer.isActive():
                self._show_image(self.preview, im)
        else:
            self._update_onion()
        if 0 <= key[1] < len(self._frame_cells):
            self._show_image(self._frame_cells[key[1]].view, im)

    def _tell_for(self, key):
        return self._set_art_status if key[0] == "art" else self._set_walk_status

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    @staticmethod
    def _side_of(key) -> str:
        return "art" if key[0] == "art" else "walk"

    @staticmethod
    def _limit_for(im) -> int:
        try:
            w, h = im.size
        except Exception:
            return _HISTORY_MAX
        return _HISTORY_MAX_BIG if w * h > 256 * 256 else _HISTORY_MAX

    @staticmethod
    def _state_bytes(state) -> int:
        n = 0
        for im in state.get("images", []):
            n += im.size[0] * im.size[1] * 4
        return n

    def _history_bytes(self) -> int:
        total = 0
        for store in (self._undo, self._redo):
            for stack in store.values():
                for _seq, im in stack:
                    total += im.size[0] * im.size[1] * 4
        for store in (self._gundo, self._gredo):
            for stack in store.values():
                for entry in stack:
                    total += self._state_bytes(entry["state"])
        return total

    def _trim_history(self) -> None:
        """总字节超上限时丢最老的记录（先丢重做，再按序号从老到新丢撤销）。"""
        while self._history_bytes() > _HISTORY_BYTES:
            if any(self._redo.values()) or any(self._gredo.values()):
                self._redo.clear()
                for stack in self._gredo.values():
                    stack.clear()
                continue
            oldest = None
            for key, stack in self._undo.items():
                if stack and (oldest is None or stack[0][0] < oldest[0]):
                    oldest = (stack[0][0], self._undo, key)
            for side, stack in self._gundo.items():
                if stack and (oldest is None or stack[0]["seq"] < oldest[0]):
                    oldest = (stack[0]["seq"], self._gundo, side)
            if oldest is None:
                return
            del oldest[1][oldest[2]][0]

    def _push_undo(self, key, im) -> None:
        if im is None:
            return
        stack = self._undo.setdefault(key, [])
        stack.append((self._next_seq(), im.copy()))
        limit = self._limit_for(im)
        while len(stack) > limit:
            del stack[0]
        self._redo.pop(key, None)
        self._gredo[self._side_of(key)].clear()
        self._trim_history()

    # ---------- 全局快照：改格子 / 改颜色 / 右↔左复制，一步撤销 ----------

    def _capture(self, side: str) -> dict:
        if side == "art":
            layers = {d: [dict(layer, image=layer["image"].copy()) for layer in ls]
                      for d, ls in self._layers.items()}
            images = [layer["image"] for ls in layers.values() for layer in ls]
            return {"cell": self._art_cell, "colors": self._art_colors, "layers": layers,
                    "active": dict(self._active_layer), "dir": self._art_dir, "images": images}
        base = self._base_im.copy()
        frames = [im.copy() for im in self._frames]
        return {"cell": self._walk_cell, "colors": self._walk_colors, "base": base, "frames": frames,
                "holds": list(self._holds()), "frame_i": self._frame_i, "images": [base] + frames}

    def _restore(self, side: str, state: dict) -> None:
        if side == "art":
            self._art_cell = state["cell"]
            self._art_colors = state["colors"]
            self._layers = {d: [dict(layer, image=layer["image"].copy()) for layer in ls]
                            for d, ls in state["layers"].items()}
            self._active_layer = dict(state["active"])
            self._art_dir = state["dir"]
            self._mark_cell("art")
            self._mark_colors("art")
            self._layer_sig_cache = None
            self._sync_art()
        else:
            self._walk_cell = state["cell"]
            self._walk_colors = state["colors"]
            self._base_im = state["base"].copy()
            self._frames[:] = [im.copy() for im in state["frames"]]
            self._frame_hold[:] = list(state["holds"])
            self._frame_i = max(0, min(state["frame_i"], len(self._frames) - 1))
            self._mark_cell("walk")
            self._mark_colors("walk")
            self._frame_sig = None
            self._stop_play()
            self._sync_walk()
        self._touch()

    def _push_global(self, side: str, label: str) -> None:
        self._commit_selection()
        self._drag = None
        stack = self._gundo[side]
        stack.append({"seq": self._next_seq(), "label": label, "state": self._capture(side)})
        state = stack[-1]["state"]
        limit = _HISTORY_MAX_BIG if any(im.size[0] * im.size[1] > 256 * 256 for im in state["images"]) else _HISTORY_MAX
        while len(stack) > limit:
            del stack[0]
        self._gredo[side].clear()
        for key in list(self._redo.keys()):
            if self._side_of(key) == side:
                del self._redo[key]
        self._trim_history()

    def _structure_changed(self, *prefixes) -> None:
        """格子、颜色、加删层或帧这类结构变化：落下选区，清掉相关撤销栈，免得撤销后错位。"""
        self._commit_selection()
        self._drag = None
        for store in (self._undo, self._redo):
            for key in list(store.keys()):
                if any(key[:len(p)] == tuple(p) for p in prefixes):
                    del store[key]

    def _undo_action(self) -> None:
        self._step_history(self._undo, self._redo, "撤销")

    def _redo_action(self) -> None:
        self._step_history(self._redo, self._undo, "重做")

    def _step_history(self, src, dst, word: str) -> None:
        """Ctrl+Z / Ctrl+Y：在当前目标的逐步记录和本页的全局快照之间按序号挑。
        撤销挑序号最大的（最近做的），重做挑序号最小的（最早撤掉的）。"""
        self._commit_selection()
        self._drag = None
        key = self._current_key()
        side = self._side_of(key)
        tell = self._tell_for(key)
        undo = src is self._undo
        gsrc = self._gundo[side] if undo else self._gredo[side]
        gdst = self._gredo[side] if undo else self._gundo[side]
        im = self._get_img(key)
        stack = src.get(key) if im is not None else None
        ks = stack[-1][0] if stack else None
        gs = gsrc[-1]["seq"] if gsrc else None
        if ks is None and gs is None:
            tell(f"没有可{word}的", "warn")
            return
        use_global = gs is not None and (ks is None or (gs > ks if undo else gs < ks))
        if use_global:
            entry = gsrc.pop()
            gdst.append({"seq": entry["seq"], "label": entry["label"], "state": self._capture(side)})
            self._restore(side, entry["state"])
            # 全局快照之后/之前的逐步记录已不再对得上，丢掉
            store = self._undo if undo else self._redo
            for k in list(store.keys()):
                if self._side_of(k) != side:
                    continue
                kept = [e for e in store[k] if (e[0] < entry["seq"] if undo else e[0] > entry["seq"])]
                if kept:
                    store[k] = kept
                else:
                    del store[k]
            self._trim_history()
            tell(f"已{word}：{entry['label']}", "ok")
            return
        seq, prev = stack.pop()
        if prev.size != im.size:
            src.pop(key, None)
            dst.pop(key, None)
            tell(f"尺寸变了，无法{word}", "warn")
            return
        other = dst.setdefault(key, [])
        other.append((seq, im.copy()))
        limit = self._limit_for(im)
        while len(other) > limit:
            del other[0]
        im.paste(prev)
        self._refresh_key(key)
        tell(f"已{word}（还剩 {len(stack)} 步）", "ok")

    # ---------- 鼠标：按下到松开算一步 ----------

    def _resolve(self, src):
        kind = src[0]
        if kind == "art":
            direction = src[1]
            if direction not in self._layers:
                return None
            if direction != self._art_dir:
                self._select_art(direction)
            layers = self._layers[direction]
            index = self._active_layer.get(direction, 0)
            if index < 0 or index >= len(layers):
                index = 0
                self._active_layer[direction] = 0
            return ("art", direction, index)
        if kind == "base":
            self._walk_focus = "base"
            return ("base",)
        self._walk_focus = "frame"
        frames = self._frames
        if self._frame_i < 0 or self._frame_i >= len(frames):
            return None
        return ("frame", self._frame_i)

    def _draw_color(self, is_bg: bool) -> tuple:
        if self._tool == "eraser":
            return (0, 0, 0, 0)
        color = tuple(int(c) for c in (self._bg if is_bg else self._fg))
        return color + (255,) if len(color) == 3 else color[:4]

    def _stamp(self, im, pts, color) -> None:
        """按画笔粗细落点；开了对称就同时落到竖中线另一侧。"""
        w, h = im.size
        n = self._brush
        off = (n - 1) // 2
        cells = set()
        for x, y in pts:
            for dx in range(n):
                for dy in range(n):
                    px, py = x - off + dx, y - off + dy
                    cells.add((px, py))
                    if self._mirror_draw:
                        cells.add((w - 1 - px, py))
        pix = im.load()
        for px, py in cells:
            if 0 <= px < w and 0 <= py < h:
                pix[px, py] = color

    def _shape_pts(self, tool, x0, y0, x1, y1) -> list:
        if tool == "line":
            return line_points(x0, y0, x1, y1)
        if tool == "rect":
            return rect_points(x0, y0, x1, y1, self._shape_fill)
        return ellipse_points(x0, y0, x1, y1, self._shape_fill)

    def _canvas_press(self, src, x: int, y: int, is_bg: bool) -> None:
        key = self._resolve(src)
        if key is None:
            return
        if self._sel is not None and self._sel["key"] != key:
            self._commit_selection()
        im = self._get_img(key)
        if im is None:
            return
        tell = self._tell_for(key)
        tool = self._tool
        if key[0] == "art":
            layer = self._layers[key[1]][key[2]]
            if not layer.get("visible", True):
                tell("这一层藏着，先打开再画", "warn")
                return
        if tool == "select":
            self._sel_press(key, im, x, y)
            return
        color = self._draw_color(is_bg)
        self._drag = {"key": key, "base": im.copy(), "start": (x, y), "last": (x, y), "color": color, "tool": tool}
        if tool in ("pencil", "eraser"):
            self._stamp(im, [(x, y)], color)
        elif tool == "fill":
            w, h = im.size
            if 0 <= x < w and 0 <= y < h:
                flood_fill(im, x, y, color)
                if self._mirror_draw:
                    flood_fill(im, w - 1 - x, y, color)
        elif tool in _SHAPES:
            self._stamp(im, self._shape_pts(tool, x, y, x, y), color)
        self._refresh_key(key)

    def _canvas_move(self, src, x: int, y: int) -> None:
        if self._tool == "select":
            if self._sel is not None and self._sel.get("mode"):
                self._sel_move(x, y)
            return
        drag = self._drag
        if drag is None or drag["key"][0] != src[0]:
            return
        key = drag["key"]
        im = self._get_img(key)
        if im is None:
            return
        tool = drag["tool"]
        if tool in ("pencil", "eraser"):
            lx, ly = drag["last"]
            if (lx, ly) == (x, y):
                return
            self._stamp(im, line_points(lx, ly, x, y), drag["color"])
            drag["last"] = (x, y)
        elif tool in _SHAPES:
            if drag["last"] == (x, y):
                return
            drag["last"] = (x, y)
            im.paste(drag["base"])
            sx, sy = drag["start"]
            self._stamp(im, self._shape_pts(tool, sx, sy, x, y), drag["color"])
        else:
            return
        self._refresh_key(key)

    def _canvas_release(self, src, x: int, y: int) -> None:
        if self._tool == "select":
            if self._sel is not None and self._sel.get("mode"):
                self._sel_release(x, y)
            return
        drag = self._drag
        if drag is None:
            return
        self._canvas_move(src, x, y)
        self._drag = None
        key = drag["key"]
        im = self._get_img(key)
        if im is not None and im.tobytes() != drag["base"].tobytes():
            self._push_undo(key, drag["base"])
            if key[0] == "frame":
                self._refresh_key(key)

    # ---------- 选区：框选 → 拖动移动 → Enter/点别处落下，Esc 取消 ----------

    def _sel_press(self, key, im, x, y) -> None:
        sel = self._sel
        if sel is not None and sel["key"] == key and sel.get("rect"):
            l, t, r, b = sel["rect"]
            if l <= x <= r and t <= y <= b:
                if sel.get("float") is None:
                    pre = im.copy()
                    sel["float"] = im.crop((l, t, r + 1, b + 1))
                    im.paste((0, 0, 0, 0), (l, t, r + 1, b + 1))
                    sel["under"] = im.copy()
                    sel["pos"] = (l, t)
                    self._push_undo(key, pre)
                    sel["pre"] = pre
                px, py = sel["pos"]
                sel["grab"] = (x - px, y - py)
                sel["mode"] = "move"
                return
        self._commit_selection()
        w, h = im.size
        cx = max(0, min(w - 1, x))
        cy = max(0, min(h - 1, y))
        self._sel = {"key": key, "rect": None, "start": (cx, cy), "mode": "marquee", "float": None, "moved": False}
        self._sync_sel_shortcuts()

    def _sel_move(self, x, y) -> None:
        sel = self._sel
        key = sel["key"]
        im = self._get_img(key)
        if im is None:
            return
        view = self._view_for(key)
        if sel["mode"] == "marquee":
            w, h = im.size
            sx, sy = sel["start"]
            cx = max(0, min(w - 1, x))
            cy = max(0, min(h - 1, y))
            if (cx, cy) != (sx, sy):
                sel["moved"] = True
            sel["rect"] = (min(sx, cx), min(sy, cy), max(sx, cx), max(sy, cy))
            view.set_marquee(sel["rect"])
            return
        gx, gy = sel["grab"]
        pos = (x - gx, y - gy)
        if pos == sel["pos"]:
            return
        sel["pos"] = pos
        fl = sel["float"]
        overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
        overlay.paste(fl, pos)
        im.paste(Image.alpha_composite(sel["under"], overlay))
        sel["rect"] = (pos[0], pos[1], pos[0] + fl.size[0] - 1, pos[1] + fl.size[1] - 1)
        view.set_marquee(sel["rect"])
        self._refresh_key(key)

    def _sel_release(self, x, y) -> None:
        sel = self._sel
        self._sel_move(x, y)
        mode = sel["mode"]
        sel["mode"] = None
        if mode == "marquee":
            if not sel.get("moved"):
                self._commit_selection()
                return
            self._tell_for(sel["key"])("拖动选区里面移动像素；Enter 或点别处落下，Esc 取消", "mut")

    def _commit_selection(self) -> None:
        sel = getattr(self, "_sel", None)
        if sel is None:
            return
        self._sel = None
        try:
            self._view_for(sel["key"]).set_marquee(None)
        except Exception:
            pass
        self._sync_sel_shortcuts()

    def _cancel_selection(self) -> None:
        sel = self._sel
        if sel is None:
            return
        key = sel["key"]
        pre = sel.get("pre")
        if pre is not None:
            im = self._get_img(key)
            if im is not None and im.size == pre.size:
                im.paste(pre)
                stack = self._undo.get(key)
                if stack:
                    stack.pop()
                self._refresh_key(key)
        self._commit_selection()
        self._tell_for(key)("已取消选区", "mut")

    def _shift_target(self, dx: int, dy: int) -> None:
        self._commit_selection()
        key = self._current_key()
        im = self._get_img(key)
        if im is None:
            return
        if self._walk_shift_all and key and key[0] == "frame" and self._on_walk_tab():
            self._push_global("walk", "全部帧平移")
            self._frames[:] = [shift_image(f, dx, dy) for f in self._frames]
            self._frame_sig = None
            self._sync_walk()
            self._set_walk_status(f"已平移全部 {len(self._frames)} 帧，可撤销", "ok")
            self._touch()
            return
        self._push_undo(key, im)
        im.paste(shift_image(im, dx, dy))
        self._refresh_key(key)

    def _composite(self, direction: str):
        return composite_layers(self._layers.get(direction) or [], self._art_cell)

    def _show_image(self, view, im) -> None:
        view.set_image(im)
        view.set_hint_visible(not frame_has_pixels(im))

    def _mark_art_selection(self) -> None:
        for direction, cell in self._dirs.items():
            cell.view.set_selected(direction == self._art_dir)
            cell.restyle()

    def _assign_art_cell(self, value) -> int:
        """改格子不拉伸：扩大补透明、缩小裁边（水平居中、底边对齐）。返回裁掉的有内容像素数。"""
        n = cell_wh(value)
        if n == self._art_cell:
            return 0
        lost = 0
        for layers in self._layers.values():
            for layer in layers:
                layer["image"], cut = recanvas(layer["image"], n)
                lost += cut
        self._art_cell = n
        return lost

    def _assign_art_colors(self, value) -> None:
        n = snap_colors(value)
        if n and n != self._art_colors:
            for layers in self._layers.values():
                for layer in layers:
                    layer["image"] = quantize_rgba(layer["image"], n)
        self._art_colors = n

    def _assign_walk_cell(self, value) -> int:
        n = cell_wh(value)
        if n == self._walk_cell:
            return 0
        self._base_im, lost = recanvas(self._base_im, n)
        frames = []
        for im in self._frames:
            out, cut = recanvas(im, n)
            frames.append(out)
            lost += cut
        self._frames[:] = frames
        self._walk_cell = n
        self._frame_sig = None
        return lost

    def _assign_walk_colors(self, value) -> None:
        n = snap_colors(value)
        if n and n != self._walk_colors:
            self._base_im = quantize_rgba(self._base_im, n)
            self._frames[:] = [quantize_rgba(im, n) for im in self._frames]
        self._walk_colors = n

    def _cell_message(self, n, lost: int) -> tuple:
        w, h = n
        if lost:
            return f"格子改为 {w}×{h}，已裁掉 {lost} 个有内容的像素，可撤销", "warn"
        return f"格子改为 {w}×{h}（不拉伸：扩大四周补透明，缩小裁边；水平居中、底边对齐），可撤销", "ok"

    def _color_message(self, n: int) -> tuple:
        if n:
            return f"已减到 {n} 色。减色不可逆，可用撤销恢复", "ok"
        return "已切回原色：已减掉的颜色不会自己回来，减色不可逆，可用撤销恢复", "warn"

    def _pick_art_cell(self, value) -> None:
        value = cell_wh(value)
        if value == self._art_cell:
            self._mark_cell("art")
            return
        self._push_global("art", "改格子")
        lost = self._assign_art_cell(value)
        self._mark_cell("art")
        self._layer_sig_cache = None
        self._sync_art()
        self._set_art_status(*self._cell_message(value, lost))
        self._touch()

    def _pick_art_colors(self, value: int) -> None:
        n = snap_colors(value)
        if n == self._art_colors:
            self._mark_colors("art")
            return
        if n:
            self._push_global("art", "改颜色")
        self._assign_art_colors(n)
        self._mark_colors("art")
        self._layer_sig_cache = None
        self._sync_art()
        self._set_art_status(*self._color_message(n))
        self._touch()

    def _pick_walk_cell(self, value) -> None:
        value = cell_wh(value)
        if value == self._walk_cell:
            self._mark_cell("walk")
            return
        self._push_global("walk", "改格子")
        lost = self._assign_walk_cell(value)
        self._mark_cell("walk")
        self._sync_walk()
        self._set_walk_status(*self._cell_message(value, lost))
        self._touch()

    def _pick_walk_colors(self, value: int) -> None:
        n = snap_colors(value)
        if n == self._walk_colors:
            self._mark_colors("walk")
            return
        if n:
            self._push_global("walk", "改颜色")
        self._assign_walk_colors(n)
        self._mark_colors("walk")
        self._sync_walk()
        self._set_walk_status(*self._color_message(n))
        self._touch()

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _layer_signature(self):
        direction = self._art_dir
        layers = self._layers[direction]
        return (
            direction,
            len(layers),
            self._active_layer.get(direction, 0),
            tuple(layer["name"] for layer in layers),
        )

    def _layer_row(self, index: int, layer: dict, active: bool) -> QWidget:
        row = _LayerRow(index)
        accent = QColor(tk("accent"))
        if active:
            row.setStyleSheet(
                f"#PixelLayerRow{{background:rgba({accent.red()},{accent.green()},{accent.blue()},0.16);"
                f"border:1px solid {tk('accent')};border-radius:6px;}}"
            )
        else:
            row.setStyleSheet(f"#PixelLayerRow{{background:transparent;border:1px solid {tk('border')};border-radius:6px;}}")
        row.setToolTip("点一下切到此层，双击改名")
        row.clicked.connect(self._select_layer)
        row.rename.connect(self._rename_layer)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(3, 2, 3, 2)
        lay.setSpacing(4)
        thumb = _LayerThumb()
        thumb.set_layer(layer)
        self._layer_thumbs[index] = thumb
        lay.addWidget(thumb, 0)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        name = QLabel(layer["name"])
        name.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        color = tk("accent") if active else tk("text")
        weight = "font-weight:600;" if active else ""
        name.setStyleSheet(f"color:{color};background:transparent;border:none;font-size:12px;{weight}")
        col.addWidget(name, 0)
        if index > 0:
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 100)
            value = int(round(float(layer.get("opacity", 1)) * 100))
            slider.setValue(value)
            slider.setFixedHeight(14)
            slider.setToolTip(f"不透明度 {value}%")
            slider.valueChanged.connect(lambda v, i=index, sl=slider: (sl.setToolTip(f"不透明度 {v}%"), self._on_opacity(i, v)))
            col.addWidget(slider, 0)
        else:
            sub = QLabel("底层 · 不透明")
            sub.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            sub.setStyleSheet(f"color:{tk('text_mut')};background:transparent;border:none;font-size:11px;")
            col.addWidget(sub, 0)
        lay.addLayout(col, 1)
        visible = layer.get("visible", True)
        eye = _IconButton("eye" if visible else "eye_off", "显示 / 隐藏此层", "small")
        eye.clicked.connect(lambda _=False, i=index, b=eye: self._toggle_eye(i, b))
        lay.addWidget(eye, 0)
        return row

    def _sync_layers(self) -> None:
        self._walk_changed()   # 图层名/显隐/不透明度/顺序变化都要刷新画板工程的 *
        sig = self._layer_signature()
        if sig == self._layer_sig_cache and self.layer_rows.count():
            return
        self._layer_sig_cache = sig
        self._clear_layout(self.layer_rows)
        self._layer_thumbs = {}
        layers = self._layers[self._art_dir]
        active = self._active_layer.get(self._art_dir, 0)
        self.lbl_layers.setText(f"图层·{DIR_LABEL[self._art_dir]}")
        for index in range(len(layers) - 1, -1, -1):
            self.layer_rows.addWidget(self._layer_row(index, layers[index], index == active))
        btns = getattr(self, "_layer_btns", {})
        if btns:
            btns["add"].setEnabled(len(layers) < _MAX_LAYERS)
            btns["del"].setEnabled(active > 0)
            btns["up"].setEnabled(0 < active < len(layers) - 1)
            btns["down"].setEnabled(active > 1)

    def _schedule_layer_thumbs(self) -> None:
        """画的时候节流：最多每 120ms 刷一次；缩略图按内容校验，没变的层不重画。"""
        timer = getattr(self, "_thumb_timer", None)
        if timer is not None and not timer.isActive():
            timer.start()

    def _update_layer_thumbs(self) -> int:
        layers = self._layers.get(self._art_dir, [])
        active = self._active_layer.get(self._art_dir, 0)
        changed = 0
        order = [active] + [i for i in self._layer_thumbs if i != active]
        for i in order:
            thumb = self._layer_thumbs.get(i)
            if thumb is not None and i < len(layers):
                changed += int(thumb.set_layer(layers[i]))
        return changed

    def _rename_layer(self, index: int) -> None:
        layers = self._layers[self._art_dir]
        if index < 0 or index >= len(layers):
            return
        text, ok = QInputDialog.getText(self, "图层改名", "新名字：", QLineEdit.Normal, layers[index]["name"])
        text = (text or "").strip()[:12]
        if not ok or not text:
            return
        layers[index]["name"] = text
        self._layer_sig_cache = None
        self._sync_layers()
        self._touch()

    def _move_layer(self, delta: int) -> None:
        """当前层上移(+1)/下移(-1)。底层固定在最下；作为全局一步可撤销，并清掉本方向的逐层撤销栈防错位。"""
        layers = self._layers[self._art_dir]
        i = self._active_layer.get(self._art_dir, 0)
        j = i + int(delta)
        if i <= 0 or j <= 0 or j >= len(layers):
            self._set_art_status("底层固定在最下；已经到头了", "warn")
            return
        self._push_global("art", "移层")
        self._structure_changed(("art", self._art_dir))
        layers[i], layers[j] = layers[j], layers[i]
        self._active_layer[self._art_dir] = j
        self._layer_sig_cache = None
        self._sync_art()
        self._set_art_status(f"「{layers[j]['name']}」已{'上' if delta > 0 else '下'}移，可撤销", "ok")

    def _select_layer(self, index: int) -> None:
        layers = self._layers[self._art_dir]
        if index < 0 or index >= len(layers):
            return
        self._commit_selection()
        self._active_layer[self._art_dir] = index
        self._layer_sig_cache = None
        self._sync_layers()

    def _toggle_eye(self, index: int, btn) -> None:
        layers = self._layers[self._art_dir]
        if index < 0 or index >= len(layers):
            return
        layer = layers[index]
        layer["visible"] = not layer.get("visible", True)
        btn.set_icon("eye" if layer["visible"] else "eye_off")
        self._refresh_art_dir(self._art_dir)
        thumb = self._layer_thumbs.get(index)
        if thumb is not None:
            thumb.set_layer(layer)

    def _on_opacity(self, index: int, value: int) -> None:
        layers = self._layers[self._art_dir]
        if index <= 0 or index >= len(layers):
            return
        layers[index]["opacity"] = max(0.0, min(1.0, int(value) / 100.0))
        self._refresh_art_dir(self._art_dir)

    def _add_layer(self) -> None:
        layers = self._layers[self._art_dir]
        if len(layers) >= _MAX_LAYERS:
            self._set_art_status("一层画板最多 6 层", "warn")
            return
        self._structure_changed(("art", self._art_dir))
        name = "上层" if len(layers) == 1 else f"第{len(layers) + 1}层"
        layers.append(self._make_layer(name))
        self._active_layer[self._art_dir] = len(layers) - 1
        self._layer_sig_cache = None
        self._sync_art()
        self._set_art_status(f"已加上「{name}」", "ok")

    def _delete_layer(self) -> None:
        layers = self._layers[self._art_dir]
        index = self._active_layer.get(self._art_dir, 0)
        if len(layers) <= 1 or index <= 0:
            self._set_art_status("底层留着", "warn")
            return
        self._structure_changed(("art", self._art_dir))
        name = layers[index]["name"]
        del layers[index]
        self._active_layer[self._art_dir] = min(index, len(layers) - 1)
        self._layer_sig_cache = None
        self._sync_art()
        self._set_art_status(f"已去掉「{name}」", "ok")

    def _quick_png(self, stem: str, im, tell) -> bool:
        if not frame_has_pixels(im):
            tell("这一块还没有图", "warn")
            return False
        folder = self._dest_dir(tell)
        if not folder:
            return False
        path = next_png_path(folder, stem)
        if not path:
            tell("序号已经用完", "warn")
            return False
        try:
            im.save(path, "PNG")
        except Exception:
            log.exception("像素画板快速保存失败")
            tell("没有写成", "err")
            return False
        tell(f"已保存：{os.path.basename(path)}", "ok")
        return True

    def _quick_save_art(self, direction: str) -> None:
        if direction not in self._layers:
            return
        self._art_dir = direction
        self._layer_sig_cache = None
        self._sync_art()
        self._quick_png(
            f"{self._art_stem()}_{DIR_LABEL[direction]}",
            self._composite(direction),
            self._set_art_status,
        )

    def _quick_save_base(self) -> None:
        self._quick_png(f"{self._walk_stem()}_底图", self._base_im, self._set_walk_status)

    def _on_fps(self, value: int) -> None:
        self._fps = int(value)
        self.lbl_fps.setText(f"{self._fps}帧/秒")
        if self._timer.isActive():
            self._timer.setInterval(self._interval_at(self._play_at))
        else:
            self._update_preview_info()
        self._touch()

    def _on_art_name(self) -> None:
        self._art_name = self.edit_art_name.text()
        self._touch()

    def _on_walk_name(self) -> None:
        self._walk_name = self.edit_walk_name.text()
        self._touch()

    def _on_art_dollar(self, *_args) -> None:
        self._art_dollar = self.chk_art_dollar.isChecked()
        self._touch()

    def _on_walk_dollar(self, *_args) -> None:
        self._walk_dollar = self.chk_walk_dollar.isChecked()
        self._touch()

    def _sync_art(self) -> None:
        for direction, cell in self._dirs.items():
            self._show_image(cell.view, self._composite(direction))
            cell.view.set_selected(direction == self._art_dir)
            cell.view.set_locked(False)
            cell.restyle()
        self._show_image(self.art_canvas, self._composite(self._art_dir))
        self._sync_layers()

    def _stop_play(self) -> None:
        self._timer.stop()
        if getattr(self, "btn_play", None) is not None:
            self.btn_play.set_icon("play")
            self.btn_play.setChecked(False) if self.btn_play.isCheckable() else None
        self._update_onion()
        self._update_preview_info()

    def _select_frame(self, index: int) -> None:
        frames = self._frames
        if index < 0 or index >= len(frames):
            return
        self._commit_selection()
        self._frame_i = index
        self._walk_focus = "frame"
        self._sync_walk()

    def _rebuild_frame_row(self) -> None:
        self._clear_layout(self.frame_row)
        self._frame_cells = []
        frames = self._frames
        for index in range(len(frames)):
            cell = _FrameCell(index)
            cell.clicked.connect(self._click_frame)
            cell.dropped.connect(self._ingest_frame)
            cell.view.single_drop = True
            cell.view.multi_refused = self._refuse_multi_drop
            cell.view.project_drop = self._drop_project
            cell.reorder.connect(self._reorder_frame)
            cell.dup_clicked.connect(lambda i: (self._select_frame(i), self._dup_frame()))
            cell.del_clicked.connect(lambda i: (self._select_frame(i), self._del_frame()))
            cell.move_clicked.connect(lambda i, d: (self._select_frame(i), self._move_frame(d)))
            self._frame_cells.append(cell)
            self.frame_row.addWidget(cell, 0)
        self.frame_row.addStretch(1)

    def _holds(self) -> list:
        """每帧时长倍数（与帧列表平行）；不够时补 1。多出的保留，兼容只存了时长的设置。"""
        frames = self._frames
        holds = self._frame_hold
        if len(holds) < len(frames):
            holds.extend([1] * (len(frames) - len(holds)))
        return holds

    def _sync_walk(self) -> None:
        frames = self._frames
        if not frames:
            frames.append(blank_cell(self._walk_cell))
        if self._frame_i >= len(frames):
            self._frame_i = len(frames) - 1
        if self._frame_i < 0:
            self._frame_i = 0
        holds = self._holds()
        sig = len(frames)
        if sig != self._frame_sig:
            self._rebuild_frame_row()
            self._frame_sig = sig
        self._show_image(self.base_view, self._base_im)
        current = frames[self._frame_i]
        self._show_image(self.editor, current)
        for cell in self._frame_cells:
            im = frames[cell.index]
            self._show_image(cell.view, im)
            cell.view.set_selected(cell.index == self._frame_i)
            hold = holds[cell.index]
            cell.set_info(hold)
            cell.restyle()
        if 0 <= self._frame_i < len(self._frame_cells):
            QTimer.singleShot(0, lambda c=self._frame_cells[self._frame_i]: self._ensure_cell_visible(c))
        if not self._timer.isActive():
            self._show_image(self.preview, current)
        self.lbl_fps.setText(f"{self._fps}帧/秒")
        self._mark_choice(self._hold_btns, holds[self._frame_i])
        self._mark_choice(self._mode_btns, self._play_mode)
        self._mark_choice(self._onion_btns, self._onion_n)
        self.btn_onion.setChecked(self._onion_on)
        self._style_choices()
        self._update_onion()
        if not self._timer.isActive():
            self._update_preview_info()
        self._walk_changed()

    def _click_frame(self, index: int) -> None:
        """点帧：选中，并把焦点给时间轴（这样 +/- 调时长，←/→ 切帧）。"""
        self._select_frame(index)
        self.frame_scroll.setFocus(Qt.MouseFocusReason)

    def _step_frame(self, delta: int) -> None:
        if not self._on_walk_tab():
            return
        j = self._frame_i + delta
        if 0 <= j < len(self._frames):
            self._select_frame(j)

    def _timeline_focused(self) -> bool:
        fw = QApplication.focusWidget()
        sc = getattr(self, "frame_scroll", None)
        return sc is not None and fw is not None and (fw is sc or sc.isAncestorOf(fw))

    def _reorder_frame(self, src: int, dst: int) -> None:
        """把 src 帧挪到 dst 位置（拖放重排）；时长、撤销栈跟着帧走。"""
        frames = self._frames
        n = len(frames)
        if not (0 <= src < n and 0 <= dst < n) or src == dst:
            return
        self._commit_selection()
        self._stop_play()
        holds = self._holds()
        order = list(range(n))
        order.insert(dst, order.pop(src))
        new_pos = {old: new for new, old in enumerate(order)}
        self._frames[:] = [frames[k] for k in order]
        holds[:n] = [holds[k] for k in order]
        for store in (self._undo, self._redo):
            moved = {}
            for old in range(n):
                v = store.pop(("frame", old), None)
                if v is not None:
                    moved[("frame", new_pos[old])] = v
            store.update(moved)
        self._frame_i = dst
        self._walk_focus = "frame"
        self._sync_walk()
        self._set_walk_status(f"已移到 {dst + 1:02d}", "ok")

    def _ensure_cell_visible(self, cell) -> None:
        try:
            if cell in self._frame_cells:
                self.frame_scroll.ensureWidgetVisible(cell, 8, 0)
        except RuntimeError:
            pass

    def _update_onion(self) -> None:
        editor = getattr(self, "editor", None)
        if editor is None:
            return
        if not self._onion_on or self._timer.isActive():
            editor.set_ghosts([])
            return
        frames = self._frames
        ghosts = []
        for k in range(self._onion_n, 0, -1):
            op = 0.35 if k == 1 else 0.18
            i = self._frame_i - k
            if 0 <= i < len(frames):
                ghosts.append((tint_ghost(frames[i], (230, 70, 70)), op))
            j = self._frame_i + k
            if 0 <= j < len(frames):
                ghosts.append((tint_ghost(frames[j], (70, 120, 240)), op))
        editor.set_ghosts(ghosts)

    def _toggle_onion(self) -> None:
        self._onion_on = not self._onion_on
        self.btn_onion.setChecked(self._onion_on)
        self._style_choices()
        self._update_onion()
        self._touch()

    def _pick_onion_n(self, n) -> None:
        self._onion_n = 2 if int(n) == 2 else 1
        self._mark_choice(self._onion_btns, self._onion_n)
        self._update_onion()
        self._touch()

    def _pick_play_mode(self, mode) -> None:
        self._play_mode = "pingpong" if mode == "pingpong" else "loop"
        self._mark_choice(self._mode_btns, self._play_mode)
        self._touch()

    def _pick_hold(self, n) -> None:
        holds = self._holds()
        holds[self._frame_i] = max(1, min(4, int(n)))
        self._sync_walk()
        if self._timer.isActive():
            self._timer.setInterval(self._interval_at(self._play_at))
        self._touch()

    def _play_sequence(self, n: int = None) -> list:
        """播放顺序。循环：0..n-1；乒乓：0..n-1..1。"""
        if n is None:
            n = len(self._frames)
        seq = list(range(n))
        if self._play_mode == "pingpong" and n > 2:
            seq += list(range(n - 2, 0, -1))
        return seq

    def _interval_at(self, pos: int) -> int:
        seq = self._play_sequence()
        holds = self._holds()
        if not seq:
            return self._interval()
        frame = seq[pos % len(seq)]
        return self._interval() * holds[frame]

    def _move_frame(self, delta: int) -> None:
        i = self._frame_i
        j = i + delta
        if j < 0 or j >= len(self._frames):
            self._set_walk_status("已经到头了", "warn")
            return
        self._reorder_frame(i, j)

    def _copy_base(self) -> None:
        frames = self._frames
        self._commit_selection()
        self._push_undo(("frame", self._frame_i), frames[self._frame_i])
        frames[self._frame_i] = self._base_im.copy()
        self._walk_focus = "frame"
        self._stop_play()
        self._sync_walk()
        self._set_walk_status(f"已把底图复制到 {self._frame_i + 1:02d}", "ok")

    def _add_frame(self) -> None:
        frames = self._frames
        if len(frames) >= _MAX_FRAMES:
            self._set_walk_status(f"一条最多 {_MAX_FRAMES} 帧", "warn")
            return
        self._stop_play()
        self._structure_changed(("frame",))
        self._holds().append(1)
        frames.append(blank_cell(self._walk_cell))
        self._frame_i = len(frames) - 1
        self._frame_sig = None
        self._sync_walk()
        self._set_walk_status(f"已加上 {self._frame_i + 1:02d} 帧", "ok")

    def _dup_frame(self) -> None:
        frames = self._frames
        if len(frames) >= _MAX_FRAMES:
            self._set_walk_status(f"一条最多 {_MAX_FRAMES} 帧", "warn")
            return
        self._stop_play()
        self._structure_changed(("frame",))
        holds = self._holds()
        holds.insert(self._frame_i + 1, holds[self._frame_i])
        frames.insert(self._frame_i + 1, frames[self._frame_i].copy())
        self._frame_i += 1
        self._frame_sig = None
        self._sync_walk()
        self._set_walk_status(f"已复制出 {self._frame_i + 1:02d} 帧", "ok")

    def _del_frame(self) -> None:
        frames = self._frames
        if len(frames) <= 1:
            self._set_walk_status("至少留一帧", "warn")
            return
        self._stop_play()
        self._structure_changed(("frame",))
        del self._holds()[self._frame_i]
        del frames[self._frame_i]
        if self._frame_i >= len(frames):
            self._frame_i = len(frames) - 1
        self._frame_sig = None
        self._sync_walk()
        self._set_walk_status(f"当前是 {self._frame_i + 1:02d}", "ok")

    def _browse_art(self) -> None:
        """第十五阶段：画板「批量导入」：小菜单选 多个文件 / 文件夹，同动效页。"""
        menu = self._make_batch_menu()
        btn = getattr(self, "btn_art_batch", None)
        pos = btn.mapToGlobal(QPoint(0, btn.height())) if btn is not None else self.cursor().pos()
        act = menu.exec_(pos)
        if act is None:
            return
        if act.data() == "files":
            paths, _ = QFileDialog.getOpenFileNames(self, "批量导入：选择图片", "", _OPEN_FILTER)
            if paths:
                self._batch_import_art(paths)
        elif act.data() == "dir":
            folder = QFileDialog.getExistingDirectory(self, "批量导入：选择文件夹", "")
            if folder:
                self._batch_import_art([folder])

    def _make_batch_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction("选择多个图片…").setData("files")
        menu.addAction("选择文件夹…").setData("dir")
        return menu

    def _refuse_art_multi_drop(self) -> None:
        self._set_art_status("拖拽一次只能放一个文件，多张请用「批量导入」", "warn")

    _art_overwrite_hook = None   # 测试用：返回 "overwrite" / "cancel"

    def _ask_art_overwrite(self, names: list) -> bool:
        hook = self._art_overwrite_hook
        if callable(hook):
            return hook(names) == "overwrite"
        dlg = _ImportDialog(
            self, "批量导入",
            f"「{'、'.join(names)}」已有内容，导入会覆盖这些方向的当前图层。要覆盖吗？",
            [("overwrite", "覆盖"), ("cancel", "取消")], self._walk_align,
        )
        for w in (dlg.lbl_align, dlg.align, dlg.lbl_align_name):
            w.setVisible(False)
        dlg.exec_()
        return dlg.choice == "overwrite"

    def _batch_import_art(self, paths) -> bool:
        """按自然排序把图片依次放进 正→左→后→右 的当前图层；多于 4 张只取前 4 张；整批一步撤销。"""
        tell = self._set_art_status
        files = self._collect_images(paths)
        if not files:
            tell("没有找到可导入的图片", "warn")
            return False
        order = list(self._board_order)
        extra = max(0, len(files) - len(order))
        files = files[:len(order)]
        loaded, bad = [], []
        for path in files:
            try:
                loaded.append((path, load_image(path)))   # 与单张拖入同一读法
            except Exception:
                bad.append(os.path.basename(path))
        if not loaded:
            tell("图片都打不开：" + "、".join(bad), "err")
            return False
        targets = order[:len(loaded)]
        busy = [DIR_LABEL[d] for d in targets
                if any(frame_has_pixels(layer.get("image")) for layer in self._layers[d])]
        if busy and not self._ask_art_overwrite(busy):
            tell("已取消批量导入", "warn")
            return False
        self._push_global("art", "批量导入四向")
        warns = 0
        for d, (path, im) in zip(targets, loaded):
            _note, warn = self._import_note(path, im, self._art_cell)
            warns += bool(warn)
            layers = self._layers[d]
            index = self._active_layer.get(d, 0)
            if index < 0 or index >= len(layers):
                index = 0
                self._active_layer[d] = 0
            layers[index]["image"] = prepare_cell(im, self._art_cell, self._art_colors)
        self._art_dir = targets[0]
        self._layer_sig_cache = None
        self._sync_art()
        msg = f"已导入 {len(loaded)} 张到 " + "→".join(DIR_LABEL[d] for d in targets)
        tips = []
        if extra:
            tips.append(f"多出的 {extra} 张没用（最多 4 个方向）")
        if bad:
            tips.append("打不开：" + "、".join(bad))
        if warns:
            tips.append(f"{warns} 张做了缩放/减色/取首帧")
        tell(msg + ("；" + "；".join(tips) if tips else "") + "；可一步撤销", "warn" if (extra or bad or warns) else "ok")
        return True

    def _swap_dirs(self, src: str, dst: str) -> None:
        """第十五阶段：缩略图拖拽——两个方向的全部图层互换（方向名不动），一步撤销。"""
        if src == dst or src not in DIRS or dst not in DIRS:
            return
        self._push_global("art", f"交换{DIR_LABEL[src]}和{DIR_LABEL[dst]}")
        L, A = self._layers, self._active_layer
        L[src], L[dst] = L[dst], L[src]
        A[src], A[dst] = A.get(dst, 0), A.get(src, 0)
        self._art_dir = dst
        self._layer_sig_cache = None
        self._sync_art()
        self._walk_changed()
        self._set_art_status(f"已交换「{DIR_LABEL[src]}」和「{DIR_LABEL[dst]}」的内容（全部图层），可撤销", "ok")

    def _browse_walk(self) -> None:
        """「批量导入」：小菜单选 多个文件 / 文件夹。"""
        menu = QMenu(self)
        a_files = menu.addAction("选择多个图片…")
        a_dir = menu.addAction("选择文件夹…")
        btn = getattr(self, "btn_walk_batch", None)
        pos = btn.mapToGlobal(QPoint(0, btn.height())) if btn is not None else self.cursor().pos()
        act = menu.exec_(pos)
        if act is a_files:
            paths, _ = QFileDialog.getOpenFileNames(self, "批量导入：选择图片", "", _OPEN_FILTER)
            if paths:
                self._batch_import(paths)
        elif act is a_dir:
            folder = QFileDialog.getExistingDirectory(self, "批量导入：选择文件夹", "")
            if folder:
                self._batch_import([folder])

    def _refuse_multi_drop(self) -> None:
        self._set_walk_status("拖拽一次只能放一个文件，多张请用「批量导入」", "warn")

    # ---------- 第十三/十四阶段：工程文件（动效 .pxa / 画板 .pxb） ----------

    def _walk_signature(self) -> bytes:
        """动效内容指纹：格子、颜色、帧/底图像素、每帧时长、名称、对齐、帧率、播放方式。选中哪帧不算改动。"""
        h = hashlib.blake2b(digest_size=16)
        edit = getattr(self, "edit_walk_name", None)
        name = edit.text() if edit is not None else self._walk_name
        meta = (tuple(self._walk_cell), self._walk_colors, len(self._frames), tuple(self._holds()[:len(self._frames)]),
                name, self._walk_align, self._fps, self._play_mode)
        h.update(repr(meta).encode("utf-8"))
        for im in [self._base_im] + list(self._frames):
            h.update(repr(im.size).encode())
            h.update(im.tobytes())
        return h.digest()

    def _art_signature(self) -> bytes:
        """画板内容指纹：格子、颜色数、名称、$ 前缀、四个方向全部图层（名称/显隐/不透明度/顺序/像素）。选中方向/图层不算改动。"""
        h = hashlib.blake2b(digest_size=16)
        edit = getattr(self, "edit_art_name", None)
        name = edit.text() if edit is not None else self._art_name
        h.update(repr((tuple(self._art_cell), self._art_colors, name, self._art_dollar)).encode("utf-8"))
        for d in DIRS:
            for layer in self._layers.get(d, []):
                h.update(repr((d, layer.get("name"), bool(layer.get("visible", True)),
                               round(float(layer.get("opacity", 1.0)), 4), layer["image"].size)).encode("utf-8"))
                h.update(layer["image"].tobytes())
        return h.digest()

    def _sig_of(self, side: str) -> bytes:
        return self._art_signature() if side == "art" else self._walk_signature()

    def _walk_changed(self) -> None:
        timer = getattr(self, "_dirty_timer", None)
        if timer is not None and not self._loading:
            timer.start()

    def _update_dirty(self, side: str = "walk") -> bool:
        """刷新两页的 * 标记，返回 side 页是否有未保存改动。"""
        if getattr(self, "_dirty_timer", None) is not None:
            self._dirty_timer.stop()
        for s in ("walk", "art"):
            st = self._proj[s]
            st["dirty"] = st["sig"] is not None and self._sig_of(s) != st["sig"]
            self._show_project_name(s)
        return self._proj[side]["dirty"]

    def _mark_project_clean(self, side: str = None) -> None:
        for s in ((side,) if side else ("walk", "art")):
            self._proj[s]["sig"] = self._sig_of(s)
            self._proj[s]["dirty"] = False
            self._show_project_name(s)
        if getattr(self, "_dirty_timer", None) is not None and not side:
            self._dirty_timer.stop()

    # 动效页沿用第十三阶段的属性名
    @property
    def _project_path(self) -> str:
        return self._proj["walk"]["path"]

    @_project_path.setter
    def _project_path(self, value) -> None:
        self._proj["walk"]["path"] = value or ""

    @property
    def _walk_dirty(self) -> bool:
        return self._proj["walk"]["dirty"]

    @property
    def _art_project_path(self) -> str:
        return self._proj["art"]["path"]

    @_art_project_path.setter
    def _art_project_path(self, value) -> None:
        self._proj["art"]["path"] = value or ""

    @property
    def _art_dirty(self) -> bool:
        return self._proj["art"]["dirty"]

    def _project_title(self, side: str = "walk") -> str:
        path = self._proj[side]["path"]
        return os.path.splitext(os.path.basename(path))[0] if path else "未命名"

    def _show_project_name(self, side: str = "walk") -> None:
        lab = getattr(self, "lbl_art_project" if side == "art" else "lbl_project", None)
        if lab is None:
            return
        st = self._proj[side]
        text = self._project_title(side) + (" *" if st["dirty"] else "")
        fm = lab.fontMetrics()
        shown = fm.elidedText(text, Qt.ElideMiddle, lab.maximumWidth())
        lab.setText(shown)
        if side == "art":   # 画板顶栏：按名字宽度留位，避免被挤成几个字
            lab.setMinimumWidth(min(lab.maximumWidth(), max(40, fm.horizontalAdvance(shown) + 6)))
        ext = PXB_EXT if side == "art" else PXA_EXT
        lab.setToolTip((st["path"] or f"未保存的新工程（{ext}）") + ("\n有未保存的改动" if st["dirty"] else ""))

    def _ask_path(self, mode: str, side: str = "walk") -> str:
        hook = self._project_path_hook
        if callable(hook):
            return str(hook(mode) or "")
        cur = self._proj[side]["path"]
        folder = os.path.dirname(cur) if cur else (self._save_path or "")
        filt, ext = (_PXB_FILTER, PXB_EXT) if side == "art" else (_PXA_FILTER, PXA_EXT)
        if mode == "open":
            path, _ = QFileDialog.getOpenFileName(self, "打开工程", folder, filt)
            return path or ""
        edit = self.edit_art_name if side == "art" else self.edit_walk_name
        stem = clean_stem(edit.text() or ("画板" if side == "art" else "角色"), False) or "工程"
        default = cur or os.path.join(folder, stem + ext)
        path, _ = QFileDialog.getSaveFileName(self, "工程另存为", default, filt)
        if path and not path.lower().endswith(ext):
            path += ext
        return path or ""

    def _make_project_prompt(self, action: str, side: str = "walk") -> _ImportDialog:
        dlg = _ImportDialog(
            self, "未保存的改动",
            f"工程「{self._project_title(side)}」有未保存的改动。{action}前要先保存吗？",
            [("save", "保存"), ("discard", "不保存"), ("cancel", "取消")], self._walk_align,
        )
        dlg.lbl_align.setVisible(False)
        dlg.align.setVisible(False)
        dlg.lbl_align_name.setVisible(False)
        return dlg

    def _confirm_discard(self, action: str, side: str = "walk") -> bool:
        """有未保存改动时问 保存 / 不保存 / 取消；返回 True 表示可以继续。"""
        if not self._update_dirty(side):
            return True
        hook = self._project_prompt_hook
        if callable(hook):
            choice = hook(action)
        else:
            dlg = self._make_project_prompt(action, side)
            dlg.exec_()
            choice = dlg.choice
        if choice == "save":
            return self._save_art_project() if side == "art" else self._save_project()
        return choice == "discard"

    def _clear_walk_history(self) -> None:
        self._structure_changed(("base",), ("frame",))
        self._gundo["walk"].clear()
        self._gredo["walk"].clear()

    def _clear_art_history(self) -> None:
        self._structure_changed(("art",))
        self._gundo["art"].clear()
        self._gredo["art"].clear()

    def _project_tell(self, side: str, path: str, e) -> None:
        msg = str(e)
        tell = self._set_art_status if side == "art" else self._set_walk_status
        if msg in WRONG_KIND.values():
            tell(msg, "warn")
        else:
            tell(f"打不开工程 {os.path.basename(path)}：{msg}", "err")

    def _color_meta(self) -> dict:
        return {
            "palette": self._palette_name,
            "custom_colors": ["#%02x%02x%02x" % tuple(c[:3]) if c else "" for c in self._custom_colors],
            "fg": "#%02x%02x%02x" % tuple(self._fg[:3]), "bg": "#%02x%02x%02x" % tuple(self._bg[:3]),
        }

    def _apply_color_meta(self, data: dict) -> None:
        pal = str(data.get("palette") or "")
        if pal in self._palette_names():
            self._palette_name = pal
        cc = data.get("custom_colors")
        if isinstance(cc, list):
            slots = [None] * _CUSTOM_SLOTS
            for i, item in enumerate(cc[:_CUSTOM_SLOTS]):
                try:
                    slots[i] = tuple(hex_rgb(str(item))[:3]) if item else None
                except Exception:
                    slots[i] = None
            self._custom_colors = slots
        for attr in ("fg", "bg"):
            try:
                if data.get(attr):
                    setattr(self, "_" + attr, tuple(hex_rgb(str(data[attr]))[:3]) + (255,))
            except Exception:
                pass

    # ----- 动效 .pxa -----

    def _new_project(self) -> bool:
        if not self._confirm_discard("新建", "walk"):
            return False
        self._stop_play()
        self._commit_selection()
        self._base_im = blank_cell(self._walk_cell)
        self._frames[:] = [blank_cell(self._walk_cell) for _ in range(3)]
        self._frame_hold[:] = [1, 1, 1]
        self._frame_i = 0
        self._walk_focus = "frame"
        self._clear_walk_history()
        self._frame_sig = None
        self._project_path = ""
        self._sync_walk()
        self._mark_project_clean("walk")
        self._set_walk_status("已新建工程（格子尺寸不变）；撤销记录已清空", "ok")
        return True

    def _project_data(self) -> dict:
        data = {
            "app": "Desktop Assistant 像素画板",
            "cell": tuple(self._walk_cell), "colors": self._walk_colors,
            "base": self._base_im, "frames": list(self._frames), "holds": list(self._holds()[:len(self._frames)]),
            "frame_i": self._frame_i, "name": self.edit_walk_name.text() or "角色",
            "align": self._walk_align, "fps": self._fps, "play_mode": self._play_mode,
            "onion_on": self._onion_on, "onion_n": self._onion_n,
        }
        data.update(self._color_meta())
        return data

    def _write_project(self, path: str, side: str = "walk") -> bool:
        self._commit_selection()
        tell = self._set_art_status if side == "art" else self._set_walk_status
        try:
            if side == "art":
                save_art_project(path, self._art_project_data())
            else:
                save_project(path, self._project_data())
        except (OSError, ProjectError, ValueError) as e:
            log.exception("保存工程失败 %s", path)
            tell(f"保存工程失败：{e}", "err")
            return False
        self._proj[side]["path"] = os.path.abspath(path)
        self._mark_project_clean(side)
        what = f"{sum(len(v) for v in self._layers.values())} 个图层" if side == "art" else f"{len(self._frames)} 帧"
        tell(f"已保存工程 {os.path.basename(path)}（{what}）", "ok")
        return True

    def _save_project(self) -> bool:
        if self._project_path:
            return self._write_project(self._project_path)
        return self._save_project_as()

    def _save_project_as(self) -> bool:
        path = self._ask_path("save", "walk")
        return self._write_project(path) if path else False

    def _open_project_dialog(self) -> bool:
        path = self._ask_path("open", "walk")
        return self._open_project(path) if path else False

    def _drop_project(self, path: str) -> None:
        self._open_project(path)

    def _open_project(self, path: str) -> bool:
        """先完整读出并校验，失败只提示、不改当前状态；成功后再问是否保存当前改动，然后整体替换。"""
        try:
            data = load_project(path)
        except ProjectError as e:
            self._project_tell("walk", path, e)
            return False
        except Exception as e:   # 兜底：绝不让坏文件把页面弄崩
            log.exception("读取工程失败 %s", path)
            self._set_walk_status(f"打不开工程 {os.path.basename(path)}：{type(e).__name__}", "err")
            return False
        if not self._confirm_discard("打开另一个工程", "walk"):
            return False
        self._stop_play()
        self._commit_selection()
        self._loading = True
        try:
            self._walk_cell = tuple(data["cell"])
            try:
                self._walk_colors = snap_colors(data.get("colors", self._walk_colors))
            except Exception:
                pass
            self._base_im = data["base"]
            self._frames[:] = list(data["frames"])
            self._frame_hold[:] = list(data["holds"])
            self._frame_i = data["frame_i"]
            self._walk_focus = "frame"
            self._walk_name = str(data.get("name") or "角色")
            self._walk_align = norm_align(data.get("align"))
            try:
                self._fps = max(4, min(16, int(data.get("fps", self._fps))))
            except (TypeError, ValueError):
                pass
            self._play_mode = "pingpong" if data.get("play_mode") == "pingpong" else "loop"
            self._onion_on = bool(data.get("onion_on", self._onion_on))
            self._onion_n = 2 if str(data.get("onion_n", self._onion_n)) == "2" else 1
            self._apply_color_meta(data)
            for w in (self.fps, self.edit_walk_name):
                w.blockSignals(True)
            self.fps.setValue(self._fps)
            self.edit_walk_name.setText(self._walk_name)
            for w in (self.fps, self.edit_walk_name):
                w.blockSignals(False)
            self._clear_walk_history()
            self._mark_cell("walk")
            self._mark_colors("walk")
            self._frame_sig = None
            self._sync_walk()
            self._sync_palette()
        finally:
            self._loading = False
        self._project_path = os.path.abspath(path)
        self._mark_project_clean("walk")
        self._touch()
        self.editor.fit_view()
        cw, ch = self._walk_cell
        self._set_walk_status(f"已打开工程 {os.path.basename(path)}：{len(self._frames)} 帧，格子 {cw}×{ch}；撤销记录已清空", "ok")
        return True

    # ----- 画板 .pxb -----

    def _art_project_data(self) -> dict:
        data = {
            "app": "Desktop Assistant 像素画板",
            "cell": tuple(self._art_cell), "colors": self._art_colors,
            "layers": {d: list(self._layers[d]) for d in DIRS},
            "active": {d: int(self._active_layer.get(d, 0)) for d in DIRS},
            "dir": self._art_dir, "name": self.edit_art_name.text() or "画板", "dollar": bool(self._art_dollar),
        }
        data.update(self._color_meta())
        return data

    def _new_art_project(self) -> bool:
        if not self._confirm_discard("新建", "art"):
            return False
        self._commit_selection()
        for d in DIRS:
            self._layers[d] = [self._make_layer("底层")]
            self._active_layer[d] = 0
        self._layer_sig_cache = None
        self._clear_art_history()
        self._art_project_path = ""
        self._sync_art()
        self._mark_project_clean("art")
        self._set_art_status("已新建画板工程（格子尺寸不变）；撤销记录已清空", "ok")
        return True

    def _save_art_project(self) -> bool:
        if self._art_project_path:
            return self._write_project(self._art_project_path, "art")
        return self._save_art_project_as()

    def _save_art_project_as(self) -> bool:
        path = self._ask_path("save", "art")
        return self._write_project(path, "art") if path else False

    def _open_art_project_dialog(self) -> bool:
        path = self._ask_path("open", "art")
        return self._open_art_project(path) if path else False

    def _drop_art_project(self, path: str) -> None:
        self._open_art_project(path)

    def _open_art_project(self, path: str) -> bool:
        try:
            data = load_art_project(path)
        except ProjectError as e:
            self._project_tell("art", path, e)
            return False
        except Exception as e:
            log.exception("读取画板工程失败 %s", path)
            self._set_art_status(f"打不开工程 {os.path.basename(path)}：{type(e).__name__}", "err")
            return False
        if not self._confirm_discard("打开另一个工程", "art"):
            return False
        self._commit_selection()
        self._loading = True
        try:
            self._art_cell = tuple(data["cell"])
            try:
                self._art_colors = snap_colors(data.get("colors", self._art_colors))
            except Exception:
                pass
            self._layers = {d: list(data["layers"][d]) for d in DIRS}
            self._active_layer = dict(data["active"])
            self._art_dir = data["dir"]
            self._art_name = str(data.get("name") or "画板")
            self._art_dollar = bool(data.get("dollar", self._art_dollar))
            self._apply_color_meta(data)
            for w in (self.edit_art_name, self.chk_art_dollar):
                w.blockSignals(True)
            self.edit_art_name.setText(self._art_name)
            self.chk_art_dollar.setChecked(self._art_dollar)
            for w in (self.edit_art_name, self.chk_art_dollar):
                w.blockSignals(False)
            self._clear_art_history()
            self._mark_cell("art")
            self._mark_colors("art")
            self._layer_sig_cache = None
            self._sync_art()
            self._sync_palette()
        finally:
            self._loading = False
        self._art_project_path = os.path.abspath(path)
        self._mark_project_clean("art")
        self._touch()
        self.art_canvas.fit_view()
        cw, ch = self._art_cell
        n = sum(len(v) for v in self._layers.values())
        self._set_art_status(f"已打开工程 {os.path.basename(path)}：四向共 {n} 个图层，格子 {cw}×{ch}；撤销记录已清空", "ok")
        return True

    # ---------- 第十一阶段：尺寸比对弹窗 ----------

    def _ask(self, kind: str, info: dict) -> dict:
        """kind: size（单张）/ batch（批量预扫描）/ group（多组）。测试可用 _import_hook 直接给答案。"""
        if callable(self._import_hook):
            res = self._import_hook(kind, info)
            return res if isinstance(res, dict) else {"choice": res, "align": self._walk_align, "remember": False}
        dlg = self._make_import_dialog(kind, info)
        dlg.exec_()
        return dlg.result_dict()

    def _make_import_dialog(self, kind: str, info: dict) -> _ImportDialog:
        cw, ch = self._walk_cell
        if kind == "size":
            w, h = info["size"]
            fits = w <= cw and h <= ch
            keep = "不改变母板（图片按所选位置放置）" if fits else "不改变（超出被切）"
            tail = "图片比母板小，不改变时四周补透明。" if fits else "图片比母板大，不改变时超出部分会被切掉。"
            return _ImportDialog(self, "尺寸不一致", f"图片 {w}×{h} 与当前母板 {cw}×{ch} 不一致。{tail}",
                                 [("change", "改变母板尺寸"), ("keep", keep), ("cancel", "取消")],
                                 self._walk_align, remember=bool(info.get("remember")))
        if kind == "batch":
            sizes = "、".join(f"{w}×{h}（{n} 张）" for (w, h), n in info["sizes"])
            mw, mh = info["max"]
            fw, fh = info["first"]
            return _ImportDialog(self, "批量导入：尺寸不一致",
                                 f"这批图片有 {len(info['sizes'])} 种尺寸：{sizes}。当前母板 {cw}×{ch}。",
                                 [("max", f"母板改为最大范围 {mw}×{mh}（推荐）"), ("first", f"母板改为第一张尺寸 {fw}×{fh}"),
                                  ("keep", "保持母板，超出被切" if any(w > cw or h > ch for (w, h), _ in info["sizes"])
                                   else "保持母板（不裁切，按所选位置放置）"), ("cancel", "取消")], self._walk_align)
        groups = "、".join(f"{g}（{len(ps)} 帧）" for g, ps in info["groups"])
        dlg = _ImportDialog(self, "检测到多组", f"检测到多组：{groups}。导入哪一组？",
                            [(g, g) for g, _ in info["groups"]] + [("*all", "全部按顺序导入"), ("cancel", "取消")], self._walk_align)
        dlg.lbl_align.setVisible(False)   # 选组不涉及对齐
        dlg.align.setVisible(False)
        dlg.lbl_align_name.setVisible(False)
        return dlg

    def _walk_blank(self) -> bool:
        return not frame_has_pixels(self._base_im) and not any(frame_has_pixels(f) for f in self._frames)

    def _place_walk(self, key, im, path: str) -> bool:
        """单张放入底图/某帧：尺寸不同按弹窗处理；返回是否放入。"""
        note, warn = self._import_note(path, im, None)
        size = im.size
        tell = self._set_walk_status
        where = "底图" if key[0] == "base" else f"{key[1] + 1:02d}"
        self._commit_selection()
        if size == self._walk_cell:
            self._push_undo(key, self._get_img(key))
            self._set_walk_img(key, quantize_rgba(place_image(im, size)[0], self._walk_colors))
            tell(f"已放入{where}{note}", "warn" if warn else "ok")
            return True
        if self._walk_blank():
            ans = {"choice": "change", "align": self._walk_align}
            silent = True
        else:
            silent = False
            ans = self._import_policy or self._ask("size", {"size": size, "remember": True})
            if ans.get("remember") and ans.get("choice") in ("change", "keep"):
                self._import_policy = dict(ans)
        choice = ans.get("choice")
        if choice not in ("change", "keep"):
            tell("已取消导入", "mut")
            return False
        self._walk_align = norm_align(ans.get("align")) if ans.get("align") else self._walk_align
        if choice == "change":
            self._push_global("walk", "导入改母板")
            lost = self._assign_walk_cell(size)
            self._mark_cell("walk")
            self._set_walk_img(key, quantize_rgba(place_image(im, size)[0], self._walk_colors))
            w, h = size
            head = f"母板设为 {w}×{h}（按首张图）" if silent else f"母板改为 {w}×{h}"
            extra = f"，旧帧裁掉 {lost} 像素" if lost else ""
            tell(f"{head}{extra}，已放入{where}，可撤销{note}", "warn" if (warn or lost) else "ok")
        else:
            self._push_undo(key, self._get_img(key))
            out, lost = place_image(im, self._walk_cell, self._walk_align)
            self._set_walk_img(key, quantize_rgba(out, self._walk_colors))
            fits = im.size[0] <= self._walk_cell[0] and im.size[1] <= self._walk_cell[1]
            cut = (f"超出部分被切掉 {lost} 个有内容的像素" if lost else
                   "四周补透明" if fits else "超出部分只有透明像素，没有裁掉内容")
            tell(f"已放入{where}（{self._walk_align}）：{cut}{note}", "warn" if (warn or lost) else "ok")
        self._touch()
        return True

    def _set_walk_img(self, key, im) -> None:
        if key[0] == "base":
            self._base_im = im
            self._walk_focus = "base"
        else:
            self._frames[key[1]] = im
            self._frame_i = key[1]
            self._walk_focus = "frame"
            self._stop_play()
        self._sync_walk()

    def _collect_images(self, paths) -> list:
        out = []
        for p in paths:
            if os.path.isdir(p):
                for name in os.listdir(p):
                    full = os.path.join(p, name)
                    if os.path.isfile(full) and os.path.splitext(name)[1].lower() in _IMAGE_EXTS:
                        out.append(full)
            elif os.path.isfile(p) and os.path.splitext(p)[1].lower() in _IMAGE_EXTS:
                out.append(p)
        return sorted(dict.fromkeys(out), key=natural_key)

    def _batch_import(self, paths) -> bool:
        """批量导入：自然排序 → 多组选一组 → 预扫描尺寸一次弹窗 → 从当前帧起覆盖/追加，一步撤销。"""
        tell = self._set_walk_status
        files = self._collect_images(paths)
        if not files:
            tell("没有找到可导入的图片", "warn")
            return False
        groups = group_paths(files)
        group_name = groups[0][0] if len(groups) == 1 else ""
        if len(groups) > 1:
            ans = self._ask("group", {"groups": groups})
            pick = ans.get("choice")
            if pick == "*all":
                files = [p for _, ps in groups for p in ps]
            elif pick in dict(groups):
                files = dict(groups)[pick]
                group_name = pick
            else:
                tell("已取消导入", "mut")
                return False
        start = self._frame_i
        room = _MAX_FRAMES - start
        dropped = max(0, len(files) - room)
        files = files[:room]
        images = []
        for p in files:
            try:
                images.append((p, load_image(p)))
            except Exception:
                log.exception("批量导入打不开图片")
        if not images:
            tell("这些图都打不开", "err")
            return False
        sizes = {}
        for _, im in images:
            sizes[im.size] = sizes.get(im.size, 0) + 1
        mw = max(w for w, _ in sizes); mh = max(h for _, h in sizes)
        first = images[0][1].size
        target = self._walk_cell
        if len(sizes) == 1 and first == self._walk_cell:
            pass
        elif len(sizes) == 1 and self._walk_blank():
            target = first
        else:
            ans = self._ask("batch", {"sizes": sorted(sizes.items(), key=lambda kv: (-kv[1], kv[0])),
                                      "max": (mw, mh), "first": first})
            choice = ans.get("choice")
            if choice not in ("max", "first", "keep"):
                tell("已取消导入", "mut")
                return False
            if ans.get("align"):
                self._walk_align = norm_align(ans["align"])
            target = (mw, mh) if choice == "max" else first if choice == "first" else self._walk_cell
        self._stop_play()
        self._push_global("walk", "批量导入")
        self._structure_changed(("frame",))
        if target != self._walk_cell:
            self._assign_walk_cell(cell_wh(target))
            self._mark_cell("walk")
        target = self._walk_cell
        holds = self._holds()
        lost = 0
        for k, (_, im) in enumerate(images):
            out, cut = place_image(im, target, self._walk_align)
            lost += cut
            out = quantize_rgba(out, self._walk_colors)
            i = start + k
            if i < len(self._frames):
                self._frames[i] = out
            else:
                self._frames.append(out)
                holds.append(1)
        self._frame_i = start
        self._walk_focus = "frame"
        self._frame_sig = None
        if group_name and self.edit_walk_name.text().strip() in ("", "角色"):
            self.edit_walk_name.setText(group_name)
            self._walk_name = group_name
        self._sync_walk()
        w, h = target
        msg = f"已导入 {len(images)} 张到 {start + 1:02d}–{start + len(images):02d}，母板 {w}×{h}（{self._walk_align}，1:1 不缩放）"
        if lost:
            msg += f"，超出被切 {lost} 像素"
        if dropped:
            msg += f"；超过 {_MAX_FRAMES} 帧上限，后 {dropped} 张未导入"
        msg += "，一步可撤销"
        tell(msg, "warn" if (lost or dropped) else "ok")
        self._touch()
        return True

    def _import_note(self, path: str, im, cell) -> tuple:
        """导入提示：没有透明底 / 放大缩小 / 多帧只取第 1 帧。返回 (追加文字, 是否警告)。"""
        parts, warn = [], False
        if not has_transparency(im):
            parts.append("这张图没有透明底")
            warn = True
        text, w = fit_note(im, cell) if cell is not None else ("", False)
        if text:
            parts.append(text)
            warn = warn or w
        n = frame_count(path)
        if n > 1:
            parts.append(f"这是多帧动图，只导入了第 1 帧（共 {n} 帧）")
            warn = True
        return ("。" + "；".join(parts)) if parts else "", warn

    def _load_dropped(self, path: str, tell):
        try:
            return load_image(path)
        except Exception:
            log.exception("像素画板打不开图片")
            tell("打不开这张图", "err")
            return None

    def _ingest_art(self, direction: str, path: str) -> None:
        if direction not in DIRS:
            direction = self._art_dir
        im = self._load_dropped(path, self._set_art_status)
        if im is None:
            return
        note, warn = self._import_note(path, im, self._art_cell)
        prepared = prepare_cell(im, self._art_cell, self._art_colors)
        self._art_dir = direction
        layers = self._layers[direction]
        index = self._active_layer.get(direction, 0)
        if index < 0 or index >= len(layers):
            index = 0
            self._active_layer[direction] = 0
        self._commit_selection()
        self._push_undo(("art", direction, index), layers[index]["image"])
        layers[index]["image"] = prepared
        self._layer_sig_cache = None
        self._sync_art()
        tone = "warn" if warn else "ok"
        self._set_art_status(f"已放入「{DIR_LABEL[direction]}」的{layers[index]['name']}{note}", tone)

    def _ingest_base(self, path: str) -> None:
        im = self._load_dropped(path, self._set_walk_status)
        if im is None:
            return
        self._import_policy = None   # 每次新的拖入/打开重新询问
        self._place_walk(("base",), im, path)

    def _ingest_frame(self, index: int, path: str) -> None:
        im = self._load_dropped(path, self._set_walk_status)
        if im is None:
            return
        if index < 0 or index >= len(self._frames):
            return
        self._import_policy = None
        self._place_walk(("frame", index), im, path)

    def _interval(self) -> int:
        return max(40, int(round(1000 / max(1, self._fps))))

    def _toggle_play(self) -> None:
        if self._timer.isActive():
            self._stop_play()
            return
        frames = self._frames
        if not any(frame_has_pixels(fr) for fr in frames):
            self._set_walk_status("这条还没有图", "warn")
            return
        self._play_at = 0
        self._show_image(self.preview, frames[0])
        self.btn_play.set_icon("stop")
        self._timer.start(self._interval_at(0))
        self._update_onion()
        self._update_preview_info(0)

    def _on_tick(self) -> None:
        frames = self._frames
        if not frames:
            self._stop_play()
            return
        seq = self._play_sequence()
        self._play_at = (self._play_at + 1) % len(seq)
        self._show_image(self.preview, frames[seq[self._play_at]])
        self._timer.setInterval(self._interval_at(self._play_at))
        self._update_preview_info(seq[self._play_at])

    def _dest_dir(self, tell):
        path = (self._save_path or "").strip()
        if not path:
            tell("还没有公共保存目录", "err")
            return None
        parent = os.path.dirname(path.rstrip("\\/"))
        if parent and not os.path.isdir(parent):
            tell("公共保存目录不存在", "err")
            return None
        try:
            os.makedirs(path, exist_ok=True)
        except Exception:
            log.exception("像素画板建目录失败")
            tell("建不了保存文件夹", "err")
            return None
        return path

    def _art_stem(self) -> str:
        return clean_stem(self.edit_art_name.text() or "画板", self.chk_art_dollar.isChecked())

    def _walk_stem(self) -> str:
        return clean_stem(self.edit_walk_name.text() or "角色", self.chk_walk_dollar.isChecked())

    def _art_has_pixels(self) -> bool:
        return any(frame_has_pixels(self._composite(direction)) for direction in DIRS)

    def _art_sheet_image(self):
        frames = {}
        for direction in DIRS:
            comp = self._composite(direction)
            frames[direction] = [comp, comp.copy(), comp.copy()]
        return compose_sheet(frames, self._art_cell)

    def _frames_have_pixels(self) -> bool:
        return any(frame_has_pixels(fr) for fr in self._frames)

    def _export_art_sheet(self) -> None:
        if not self._art_has_pixels():
            self._set_art_status("还没有图", "warn")
            return
        folder = self._dest_dir(self._set_art_status)
        if not folder:
            return
        path = next_free_path(folder, self._art_stem())
        if not path:
            self._set_art_status("同名文件太多，换个名字再存", "err")
            return
        try:
            self._art_sheet_image().save(path, "PNG")
        except Exception:
            log.exception("像素画板保存整表失败")
            self._set_art_status("整表没有写成", "err")
            return
        self._set_art_status(f"已保存整表：{os.path.basename(path)}", "ok")

    def _export_art_dirs(self) -> None:
        if not self._art_has_pixels():
            self._set_art_status("还没有图", "warn")
            return
        folder = self._dest_dir(self._set_art_status)
        if not folder:
            return
        stem = self._art_stem()
        names = []
        try:
            for direction in DIRS:
                im = self._composite(direction)
                if not frame_has_pixels(im):
                    continue
                path = next_free_path(folder, f"{stem}_{DIR_LABEL[direction]}")
                if not path:
                    continue
                im.save(path, "PNG")
                names.append(os.path.basename(path))
            count = len(names)
        except Exception:
            log.exception("像素画板保存四向失败")
            self._set_art_status("四向没有写成", "err")
            return
        self._set_art_status(f"已保存 {count} 张站立图：{'、'.join(names)}", "ok" if count else "warn")

    def _export_walk_sheet(self) -> None:
        if not self._frames_have_pixels():
            self._set_walk_status("还没有图", "warn")
            return
        folder = self._dest_dir(self._set_walk_status)
        if not folder:
            return
        path = next_free_path(folder, self._walk_stem())
        if not path:
            self._set_walk_status("同名文件太多，换个名字再存", "err")
            return
        try:
            compose_clip_sheet([self._frames], self._walk_cell).save(path, "PNG")
        except Exception:
            log.exception("像素画板保存动效整表失败")
            self._set_walk_status("整表没有写成", "err")
            return
        self._set_walk_status(f"已保存整表：{os.path.basename(path)}", "ok")

    def _export_gif(self) -> None:
        frames = list(self._frames)
        if not any(frame_has_pixels(fr) for fr in frames):
            self._set_walk_status("这条还没有图", "warn")
            return
        folder = self._dest_dir(self._set_walk_status)
        if not folder:
            return
        path = next_free_path(folder, self._walk_stem(), ".gif")
        if not path:
            self._set_walk_status("同名文件太多，换个名字再存", "err")
            return
        holds = self._holds()
        seq = self._play_sequence(len(frames))
        try:
            write_gif_timed(
                [frames[i] for i in seq], path, [self._interval() * holds[i] for i in seq],
            )
        except Exception:
            log.exception("像素画板保存 GIF 失败")
            self._set_walk_status("GIF 没有写成", "err")
            return
        self._set_walk_status(f"已保存 GIF：{os.path.basename(path)}", "ok")

    def _export_frames(self) -> None:
        if not self._frames_have_pixels():
            self._set_walk_status("还没有图", "warn")
            return
        folder = self._dest_dir(self._set_walk_status)
        if not folder:
            return
        out_dir = next_free_path(folder, self._walk_stem() + "_逐帧", "")   # 每次新建带编号的文件夹，不留旧文件
        if not out_dir:
            self._set_walk_status("同名文件夹太多，换个名字再存", "err")
            return
        try:
            os.makedirs(out_dir, exist_ok=True)
            count = 0
            stem = self._walk_stem()
            for index, fr in enumerate(self._frames):
                if not frame_has_pixels(fr):
                    continue
                fr.save(os.path.join(out_dir, f"{stem}_{index + 1:02d}.png"), "PNG")
                count += 1
        except Exception:
            log.exception("像素画板保存逐帧失败")
            self._set_walk_status("逐帧没有写成", "err")
            return
        self._set_walk_status(f"已保存 {count} 张逐帧到文件夹：{os.path.basename(out_dir)}", "ok")

    def _stored_stride(self, value) -> int:
        try:
            n = int(value)
        except (TypeError, ValueError):
            return 2
        return max(0, min(3, n))

    def _stored_leg(self, value) -> int:
        try:
            n = float(value)
        except (TypeError, ValueError):
            return 42
        if n <= 1:
            n *= 100.0
        return max(20, min(70, int(round(n))))

    @staticmethod
    def _cell_from(data: dict, prefix: str):
        """新键 {prefix}cell_w/h 优先；否则旧的 {prefix}cell / 旧自定义边长当正方形；都没有用 32。"""
        w, h = data.get(prefix + "cell_w"), data.get(prefix + "cell_h")
        try:
            if w and h:
                return cell_wh((int(w), int(h)))
        except (TypeError, ValueError):
            pass
        for key in (prefix + "cell", "cell", prefix + "custom_cell"):
            v = data.get(key)
            if v:
                try:
                    return cell_wh(int(v))
                except (TypeError, ValueError):
                    continue
        return (32, 32)

    def apply_settings(self, data: dict) -> None:
        data = data or {}
        self._loading = True
        try:
            self._save_path = str(data.get("save_path") or "").replace("\\", "/")
            self._assign_art_cell(self._cell_from(data, "art_"))
            self._assign_art_colors(data.get("art_colors", data.get("colors", 0)))
            self._assign_walk_cell(self._cell_from(data, ""))
            self._assign_walk_colors(data.get("colors", 0))
            self._keep_stride = self._stored_stride(data.get("stride", self._keep_stride))
            self._keep_leg = self._stored_leg(data.get("leg", self._keep_leg))
            self._keep_four = bool(data.get("four", self._keep_four))
            try:
                self._fps = max(4, min(16, int(data.get("fps", 8))))
            except (TypeError, ValueError):
                self._fps = 8
            self._art_dollar = bool(data.get("art_dollar", data.get("dollar", True)))
            self._walk_dollar = bool(data.get("dollar", True))
            self._art_name = str(data.get("art_name") or "画板")
            # 第一阶段新增：读不到用默认值
            self._grid_on = bool(data.get("grid_on", True))
            self._mirror_draw = bool(data.get("mirror_draw", False))
            self._shape_fill = bool(data.get("shape_fill", False))
            try:
                self._brush = max(1, min(3, int(data.get("brush", 1))))
            except (TypeError, ValueError):
                self._brush = 1
            # 旧版的 ref_dir / ref_flip（参考叠影，已删除）直接忽略
            self._onion_on = bool(data.get("onion_on", False))
            self._onion_n = 2 if str(data.get("onion_n", 1)) == "2" else 1
            self._play_mode = "pingpong" if data.get("play_mode") == "pingpong" else "loop"
            self._preview_big = bool(data.get("walk_preview_big", False))
            if getattr(self, "walk_side", None) is not None:
                self._apply_preview_size()
            # 新结构 frame_holds（列表）；旧结构 frame_hold 按动作分，取「走」那条
            raw = data.get("frame_holds")
            if not isinstance(raw, list):
                old = data.get("frame_hold")
                raw = old.get("走") if isinstance(old, dict) else None
            vals = []
            for v in raw if isinstance(raw, list) else []:
                try:
                    vals.append(max(1, min(4, int(v))))
                except (TypeError, ValueError):
                    vals.append(1)
            self._frame_hold[:] = vals[:_MAX_FRAMES]
            self._holds()
            cc = data.get("custom_colors")
            slots = [None] * _CUSTOM_SLOTS
            if isinstance(cc, list):
                for i, item in enumerate(cc[:_CUSTOM_SLOTS]):
                    try:
                        slots[i] = tuple(hex_rgb(str(item))[:3]) if item else None
                    except Exception:
                        slots[i] = None
            self._custom_colors = slots
            pal = str(data.get("palette") or "默认64")
            self._palette_name = pal if pal in self._palette_names() else "默认64"
            tool = str(data.get("tool") or "pencil")
            self._set_tool(tool if tool in dict(_TOOLS) else "pencil")
            self._walk_name = str(data.get("name") or "角色")
            self._walk_align = norm_align(data.get("walk_align"))
            self._walk_shift_all = bool(data.get("walk_shift_all", False))
            # 第十四阶段：最近工程已删除，旧设置里的 walk_recent_projects 直接忽略
            widgets = (
                self.fps, self.chk_art_dollar, self.chk_walk_dollar,
                self.edit_art_name, self.edit_walk_name,
            )
            for widget in widgets:
                widget.blockSignals(True)
            self.fps.setValue(self._fps)
            self.chk_art_dollar.setChecked(self._art_dollar)
            self.chk_walk_dollar.setChecked(self._walk_dollar)
            self.edit_art_name.setText(self._art_name)
            self.edit_walk_name.setText(self._walk_name)
            for widget in widgets:
                widget.blockSignals(False)
            self._mark_cell("art")
            self._mark_colors("art")
            self._mark_cell("walk")
            self._mark_colors("walk")
            self._layer_sig_cache = None
            self._frame_sig = None
            self._structure_changed(("art",), ("base",), ("frame",))
            for store in (self._gundo, self._gredo):
                for stack in store.values():
                    stack.clear()
            self._sync_toolbar()
            self._sync_art()
            self._sync_walk()
            self._sync_palette()
        finally:
            self._loading = False
        self._mark_project_clean()

    def export_settings(self) -> dict:
        return {
            "save_path": self._save_path,
            "cell": max(self._walk_cell),   # 旧键：存较大边，旧版读入当正方形
            "colors": self._walk_colors,
            "stride": self._keep_stride,
            "leg": self._keep_leg,
            "fps": self._fps,
            "four": self._keep_four,
            "dollar": self.chk_walk_dollar.isChecked(),
            "name": self.edit_walk_name.text() or "角色",
            "art_cell": max(self._art_cell),
            "art_colors": self._art_colors,
            "art_dollar": self.chk_art_dollar.isChecked(),
            "art_name": self.edit_art_name.text() or "画板",
            "grid_on": self._grid_on,
            "mirror_draw": self._mirror_draw,
            "shape_fill": self._shape_fill,
            "brush": self._brush,
            "tool": self._tool,
            "onion_on": self._onion_on,
            "onion_n": self._onion_n,
            "play_mode": self._play_mode,
            "walk_preview_big": self._preview_big,
            "frame_holds": list(self._holds()),
            "custom_colors": [
                "#%02x%02x%02x" % tuple(c[:3]) if c else "" for c in self._custom_colors
            ],
            "palette": self._palette_name,
            "cell_w": self._walk_cell[0],
            "cell_h": self._walk_cell[1],
            "art_cell_w": self._art_cell[0],
            "art_cell_h": self._art_cell[1],
            "walk_align": self._walk_align,
            "walk_shift_all": self._walk_shift_all,
        }
