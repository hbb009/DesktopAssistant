# pages/page_pixel_board.py
# 像素画板：画板、行走两个分页，下面一块调色盘。
# 画板是正、左、后、右，每块可以分层，上层能改透明度。
# 行走以一张底图做出 走 / 跑 / 跳，帧序是 01、02、03。

from __future__ import annotations

import os

from PyQt5.QtCore import Qt, QRect, QSize, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QImage, QPainter, QPen
from PyQt5.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSizePolicy, QSlider, QTabWidget, QVBoxLayout, QWidget,
)

from PIL import Image

from styles.style_all import TAB_QSS, VIDEO_TAB_QSS, fmt, theme, tk
from utils.logger import get_logger
from utils.pixel_walk import (
    CELLS, DIR_LABEL, DIRS, PALETTES, blank_cell, clean_stem, compose_clip_sheet,
    compose_sheet, composite_layers, flood_fill, frame_has_pixels, has_transparency,
    hex_rgb, load_image, next_png_path, prepare_cell, quantize_rgba, resize_cell,
    snap_cell, snap_colors, write_gif,
)

_CLIPS = ("走", "跑", "跳")
_TOOLS = (("pencil", "铅笔"), ("eraser", "橡皮"), ("fill", "填充"), ("pick", "取色"))
_MAX_LAYERS = 6
_MAX_FRAMES = 8

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

    def dragEnterEvent(self, event):
        _accept_image_drag(event)

    def dragMoveEvent(self, event):
        _accept_image_drag(event)

    def dropEvent(self, event):
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
            x = (self.width() - dw) // 2
            y = (self.height() - dh) // 2
            box = QRect(x, y, dw, dh)
            _paint_checker(p, box)
            p.drawImage(box, self._img)
        color = QColor(tk("accent") if self._selected else tk("border"))
        pen = QPen(color)
        pen.setWidth(2 if self._selected else 1)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        inset = 1
        p.drawRect(self.rect().adjusted(inset, inset, -inset - 1, -inset - 1))
        if self._locked:
            p.setPen(QColor(tk("accent")))
            font = QFont("等线")
            font.setPixelSize(11)
            p.setFont(font)
            p.drawText(8, 16, "改")
        empty = self._img is None or self._img.isNull()
        if self._hint and (empty or self._force_hint):
            p.setPen(QColor(tk("text_mut")))
            font = QFont("等线")
            font.setPixelSize(13)
            p.setFont(font)
            p.drawText(self.rect(), int(Qt.AlignCenter), self._hint)
        if self._show_zoom and self._img is not None and not self._img.isNull():
            p.setPen(QColor(tk("text_dim")))
            font = QFont("等线")
            font.setPixelSize(11)
            p.setFont(font)
            p.drawText(8, self.height() - 8, f"×{zoom}")
        p.end()


class _PixelCanvas(_SpriteView):
    """能把鼠标落点换算成格子里的像素。"""

    stroke = pyqtSignal(int, int, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_paint = True
        self._pressed = False
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.NoContextMenu)

    def set_drag_paint(self, on: bool) -> None:
        self._drag_paint = bool(on)

    def _cell_pos(self, pos):
        if self._img is None or self._img.isNull():
            return None
        iw, ih = self._img.width(), self._img.height()
        zoom = max(1, min(self.width() // max(1, iw), self.height() // max(1, ih)))
        dw, dh = iw * zoom, ih * zoom
        ox = (self.width() - dw) // 2
        oy = (self.height() - dh) // 2
        x = pos.x() - ox
        y = pos.y() - oy
        if x < 0 or y < 0 or x >= dw or y >= dh:
            return None
        return int(x // zoom), int(y // zoom)

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

    def mousePressEvent(self, event):
        if event.button() in (Qt.LeftButton, Qt.RightButton):
            self._pressed = True
            self._emit_stroke(event, False)
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pressed:
            self._emit_stroke(event, True)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._pressed = False
        super().mouseReleaseEvent(event)


class _SwatchGrid(QWidget):
    """色块格子。左键前景，右键背景。"""

    picked = pyqtSignal(tuple, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._colors = []
        self._fg = (0, 0, 0)
        self._bg = (255, 255, 255)
        self.setMinimumHeight(72)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setContextMenuPolicy(Qt.NoContextMenu)

    def set_colors(self, colors, fg, bg) -> None:
        self._colors = [tuple(c[:3]) for c in colors]
        self._fg = tuple(fg[:3])
        self._bg = tuple(bg[:3])
        self.update()

    def _index_at(self, pos):
        n = len(self._colors)
        if n <= 0 or self.width() <= 0 or self.height() <= 0:
            return None
        cols = max(8, self.width() // 28)
        rows = max(1, (n + cols - 1) // cols)
        cw = self.width() / float(cols)
        ch = self.height() / float(rows)
        col = int(pos.x() / cw)
        row = int(pos.y() / ch)
        index = row * cols + col
        if index < 0 or index >= n:
            return None
        return index

    def mousePressEvent(self, event):
        index = self._index_at(event.pos())
        if index is None:
            return
        bg = event.button() == Qt.RightButton
        self.picked.emit(self._colors[index], bg)

    def paintEvent(self, event):
        del event
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(tk("panel_2")))
        n = len(self._colors)
        if n <= 0:
            p.end()
            return
        cols = max(8, self.width() // 28)
        rows = max(1, (n + cols - 1) // cols)
        cw = self.width() / float(cols)
        ch = self.height() / float(rows)
        for i, rgb in enumerate(self._colors):
            col = i % cols
            row = i // cols
            rect = QRect(int(col * cw) + 2, int(row * ch) + 2, max(4, int(cw) - 4), max(4, int(ch) - 4))
            p.fillRect(rect, QColor(*rgb))
            if rgb == self._fg or rgb == self._bg:
                pen = QPen(QColor(tk("accent") if rgb == self._fg else tk("text")))
                pen.setWidth(2 if rgb == self._fg else 1)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawRect(rect.adjusted(0, 0, -1, -1))
        p.end()


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

    def __init__(self, direction: str, parent=None):
        super().__init__(parent)
        self.direction = direction
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.view = _PixelCanvas()
        self.view.setMinimumSize(100, 120)
        self.view.set_hint("拖入")
        self.view.clicked.connect(lambda: self.clicked.emit(self.direction))
        self.view.dropped.connect(lambda path: self.dropped.emit(self.direction, path))
        self.view.stroke.connect(
            lambda x, y, bg: self.stroke.emit(self.direction, x, y, bg)
        )
        self.lbl = QLabel(DIR_LABEL[direction])
        self.lbl.setAlignment(Qt.AlignCenter)
        self.btn_save = QPushButton("保存")
        self.btn_save.setProperty("kind", "medium")
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_save.setFocusPolicy(Qt.NoFocus)
        self.btn_save.setFixedHeight(24)
        self.btn_save.clicked.connect(lambda: self.save_clicked.emit(self.direction))
        lay.addWidget(self.view, 1)
        lay.addWidget(self.lbl, 0)
        lay.addWidget(self.btn_save, 0)

    def restyle(self) -> None:
        color = tk("accent") if self.view._selected else tk("text")
        weight = "font-weight:600;" if self.view._selected else ""
        self.lbl.setStyleSheet(
            f"color:{color};background:transparent;font-size:14px;{weight}"
        )
        self.view.update()


class _FrameCell(QWidget):
    clicked = pyqtSignal(int)
    dropped = pyqtSignal(int, str)

    def __init__(self, index: int, parent=None):
        super().__init__(parent)
        self.index = index
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.view = _SpriteView()
        self.view.setMinimumSize(64, 72)
        self.view.set_hint("拖入")
        self.view.clicked.connect(lambda: self.clicked.emit(self.index))
        self.view.dropped.connect(lambda path: self.dropped.emit(self.index, path))
        self.lbl = QLabel("")
        self.lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.lbl, 0)

    def restyle(self) -> None:
        self.lbl.setStyleSheet(f"color:{tk('text_mut')};background:transparent;font-size:12px;")
        self.view.update()


class PagePixelBoard(QWidget):
    def hasHeightForWidth(self):
        return False

    def minimumSizeHint(self):
        return QSize(0, 0)

    def __init__(self):
        super().__init__()
        self.setObjectName("PageRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._art_cell = 48
        self._art_colors = 0
        self._walk_cell = 48
        self._walk_colors = 0
        self._art_dir = "down"
        self._board_order = ("down", "left", "up", "right")
        self._layers = {}
        self._active_layer = {}
        for direction in DIRS:
            self._layers[direction] = [self._make_layer("底层")]
            self._active_layer[direction] = 0
        self._layer_sig_cache = None
        self._base_im = blank_cell(self._walk_cell)
        self._clips = {
            name: [blank_cell(self._walk_cell) for _ in range(3)] for name in _CLIPS
        }
        self._clip = "走"
        self._frame_i = 0
        self._tool = "pencil"
        self._fg = (26, 28, 44, 255)
        self._bg = (244, 244, 244, 255)
        self._palette_name = "经典"
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
        self.tabs.addTab(self.page_walk, "行走")
        self.tabs.setCurrentIndex(0)
        self.tabs.blockSignals(False)
        self.tabs.currentChanged.connect(lambda _i: self._refresh_tabs())
        root.addWidget(self.tabs, 7)
        self.palette = self._build_palette()
        root.addWidget(self.palette, 3)
        self.tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        self.palette.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)

        self._apply_tab_chrome()
        self._apply_palette_chrome()
        self._set_tool("pencil")
        self._style_choices()
        self._sync_art()
        self._sync_walk()
        self._sync_palette()
        theme.changed.connect(self.refresh_theme)

    def _make_layer(self, name: str) -> dict:
        return {
            "name": name,
            "image": blank_cell(getattr(self, "_art_cell", 48)),
            "opacity": 1.0,
            "visible": True,
        }

    def set_prefs_dirty_callback(self, fn) -> None:
        self._prefs_dirty = fn

    def shutdown(self) -> None:
        self._timer.stop()

    def _touch(self) -> None:
        if self._loading:
            return
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
        tools = QHBoxLayout()
        tools.setSpacing(8)
        self._art_cell_btns = {}
        tools.addLayout(self._choice_row(
            "格子", CELLS, self._art_cell, self._art_cell_btns, self._pick_art_cell, compact=True,
        ))
        self._art_color_btns = {}
        color_labels = [(0, "原色"), (16, "16色"), (32, "32色")]
        tools.addLayout(self._choice_row(
            "颜色", color_labels, self._art_colors, self._art_color_btns, self._pick_art_colors, compact=True,
        ))
        tools.addStretch(1)
        tools.addWidget(self._medium("打开图片", self._browse_art), 0)
        tools.addWidget(self._medium("把右镜像到左", self._mirror), 0)
        lay.addLayout(tools)

        mid = QHBoxLayout()
        mid.setSpacing(8)
        self._dirs = {}
        for direction in self._board_order:
            cell = _DirCell(direction)
            cell.clicked.connect(self._select_art)
            cell.dropped.connect(self._ingest_art)
            cell.save_clicked.connect(self._quick_save_art)
            cell.stroke.connect(self._on_art_stroke)
            self._dirs[direction] = cell
            mid.addWidget(cell, 1)
        mid.addWidget(self._build_layer_panel(), 0)
        lay.addLayout(mid, 1)

        self.lbl_art_spec = QLabel(
            "每块画板自己分层。上面的层能改透明度，叠在底层上。保存这一块不弹窗，同名自动加序号。"
        )
        self.lbl_art_spec.setWordWrap(True)
        lay.addWidget(self.lbl_art_spec, 0)

        out = QHBoxLayout()
        out.setSpacing(8)
        self.edit_art_name = self._name_edit(self._art_name, "画板", self._on_art_name)
        self.chk_art_dollar = self._dollar_box(self._art_dollar, self._on_art_dollar)
        out.addWidget(self.edit_art_name, 0)
        out.addWidget(self.chk_art_dollar, 0)
        out.addWidget(self._medium("保存整表", self._export_art_sheet), 0)
        out.addWidget(self._medium("保存四向", self._export_art_dirs), 0)
        self.lbl_art_status = QLabel("把图拖进画板，或直接在格子上画")
        self.lbl_art_status.setWordWrap(True)
        out.addWidget(self.lbl_art_status, 1)
        lay.addLayout(out)
        self._style_one_status(self.lbl_art_status, self._art_tone)

    def _build_layer_panel(self) -> QWidget:
        box = QWidget()
        box.setFixedWidth(236)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.lbl_layers = QLabel("分层")
        self.lbl_layers.setProperty("pixelMuted", "1")
        lay.addWidget(self.lbl_layers, 0)
        self.layer_host = QWidget()
        self.layer_rows = QVBoxLayout(self.layer_host)
        self.layer_rows.setContentsMargins(0, 0, 0, 0)
        self.layer_rows.setSpacing(4)
        lay.addWidget(self.layer_host, 0)
        lay.addStretch(1)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(self._medium("加一层", self._add_layer), 1)
        row.addWidget(self._medium("删除此层", self._delete_layer), 1)
        lay.addLayout(row)
        return box

    def _build_walk(self) -> None:
        lay = self.page_walk.layout()
        head = QHBoxLayout()
        head.setSpacing(8)

        base_col = QVBoxLayout()
        base_col.setContentsMargins(0, 0, 0, 0)
        base_col.setSpacing(2)
        self.base_view = _PixelCanvas()
        self.base_view.setFixedSize(120, 120)
        self.base_view.set_hint("拖入")
        self.base_view.dropped.connect(self._ingest_base)
        self.base_view.stroke.connect(self._on_base_stroke)
        self.lbl_base = QLabel("底图")
        self.lbl_base.setAlignment(Qt.AlignCenter)
        self.lbl_base.setProperty("pixelMuted", "1")
        self.btn_save_base = QPushButton("保存")
        self.btn_save_base.setProperty("kind", "medium")
        self.btn_save_base.setCursor(Qt.PointingHandCursor)
        self.btn_save_base.setFocusPolicy(Qt.NoFocus)
        self.btn_save_base.setFixedHeight(24)
        self.btn_save_base.clicked.connect(self._quick_save_base)
        base_col.addWidget(self.base_view, 0)
        base_col.addWidget(self.lbl_base, 0)
        base_col.addWidget(self.btn_save_base, 0)
        head.addLayout(base_col, 0)

        actions = QVBoxLayout()
        actions.setSpacing(6)
        actions.addWidget(self._medium("复制底图", self._copy_base), 0)
        actions.addWidget(self._medium("打开图片", self._browse_walk), 0)
        actions.addStretch(1)
        head.addLayout(actions, 0)

        self._clip_btns = {}
        head.addLayout(self._choice_row(
            "动作", list(_CLIPS), self._clip, self._clip_btns, self._select_clip, compact=True,
        ))
        head.addStretch(1)

        prev_col = QVBoxLayout()
        prev_col.setContentsMargins(0, 0, 0, 0)
        prev_col.setSpacing(2)
        self.preview = _SpriteView()
        self.preview.setFixedSize(120, 120)
        self.preview.setAcceptDrops(False)
        self.preview.set_hint("预览")
        prev_lbl = QLabel("预览")
        prev_lbl.setAlignment(Qt.AlignCenter)
        prev_lbl.setProperty("pixelMuted", "1")
        prev_col.addWidget(self.preview, 0)
        prev_col.addWidget(prev_lbl, 0)
        head.addLayout(prev_col, 0)

        play_col = QVBoxLayout()
        play_col.setSpacing(6)
        self.btn_play = self._medium("播放", self._toggle_play)
        play_col.addWidget(self.btn_play, 0)
        fps_row = QHBoxLayout()
        fps_row.setSpacing(6)
        self.fps = QSlider(Qt.Horizontal)
        self.fps.setRange(4, 16)
        self.fps.setValue(self._fps)
        self.fps.setFixedWidth(108)
        self.fps.valueChanged.connect(self._on_fps)
        self.lbl_fps = QLabel("")
        self.lbl_fps.setFixedWidth(64)
        fps_row.addWidget(self.fps, 0)
        fps_row.addWidget(self.lbl_fps, 0)
        play_col.addLayout(fps_row)
        play_col.addStretch(1)
        head.addLayout(play_col, 0)
        lay.addLayout(head)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._walk_cell_btns = {}
        bar.addLayout(self._choice_row(
            "格子", CELLS, self._walk_cell, self._walk_cell_btns, self._pick_walk_cell, compact=True,
        ))
        self._walk_color_btns = {}
        color_labels = [(0, "原色"), (16, "16色"), (32, "32色")]
        bar.addLayout(self._choice_row(
            "颜色", color_labels, self._walk_colors, self._walk_color_btns, self._pick_walk_colors, compact=True,
        ))
        bar.addStretch(1)
        bar.addWidget(self._medium("加一帧", self._add_frame), 0)
        bar.addWidget(self._medium("复制此帧", self._dup_frame), 0)
        bar.addWidget(self._medium("删除此帧", self._del_frame), 0)
        lay.addLayout(bar)

        self.frame_host = QWidget()
        self.frame_row = QHBoxLayout(self.frame_host)
        self.frame_row.setContentsMargins(0, 0, 0, 0)
        self.frame_row.setSpacing(8)
        self._frame_cells = []
        self._frame_sig = None
        lay.addWidget(self.frame_host, 0)

        self.editor = _PixelCanvas()
        self.editor.setMinimumHeight(100)
        self.editor.set_hint("绘制")
        self.editor.stroke.connect(self._on_editor_stroke)
        lay.addWidget(self.editor, 1)

        self.lbl_spec = QLabel("从底图复制出 01、02、03 帧，再改成走、跑、跳。播放顺序就是帧序，预览单独动。")
        self.lbl_spec.setWordWrap(True)
        lay.addWidget(self.lbl_spec, 0)

        out = QHBoxLayout()
        out.setSpacing(8)
        self.edit_walk_name = self._name_edit(self._walk_name, "角色", self._on_walk_name)
        self.chk_walk_dollar = self._dollar_box(self._walk_dollar, self._on_walk_dollar)
        out.addWidget(self.edit_walk_name, 0)
        out.addWidget(self.chk_walk_dollar, 0)
        out.addWidget(self._medium("保存整表", self._export_walk_sheet), 0)
        out.addWidget(self._medium("保存 GIF", self._export_gif), 0)
        out.addWidget(self._medium("保存逐帧", self._export_frames), 0)
        self.lbl_walk_status = QLabel("拖进一张底图，复制到帧上再画")
        self.lbl_walk_status.setWordWrap(True)
        out.addWidget(self.lbl_walk_status, 1)
        lay.addLayout(out)
        self._style_one_status(self.lbl_walk_status, self._walk_tone)

    def _build_palette(self) -> QWidget:
        box = QWidget()
        box.setObjectName("PixelPalette")
        box.setAttribute(Qt.WA_StyledBackground, True)
        box.setAutoFillBackground(True)
        box.setMinimumHeight(96)
        lay = QHBoxLayout(box)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(10)

        wells = QVBoxLayout()
        wells.setSpacing(4)
        caps = QHBoxLayout()
        caps.setSpacing(4)
        for text in ("前景", "背景"):
            lab = QLabel(text)
            lab.setAlignment(Qt.AlignCenter)
            lab.setFixedWidth(36)
            lab.setProperty("pixelMuted", "1")
            caps.addWidget(lab, 0)
        wells.addLayout(caps)
        pair = QHBoxLayout()
        pair.setSpacing(4)
        self.well_fg = _ColorWell()
        self.well_bg = _ColorWell()
        pair.addWidget(self.well_fg, 0)
        pair.addWidget(self.well_bg, 0)
        wells.addLayout(pair)
        self.btn_swap = QPushButton("对调")
        self.btn_swap.setCursor(Qt.PointingHandCursor)
        self.btn_swap.setFocusPolicy(Qt.NoFocus)
        self.btn_swap.setFixedHeight(22)
        self.btn_swap.clicked.connect(self._swap_colors)
        wells.addWidget(self.btn_swap, 0)
        self.lbl_hex = QLabel("")
        self.lbl_hex.setAlignment(Qt.AlignCenter)
        wells.addWidget(self.lbl_hex, 0)
        wells.addStretch(1)
        lay.addLayout(wells, 0)

        side = QVBoxLayout()
        side.setSpacing(6)
        self._tool_btns = {}
        side.addLayout(self._choice_row(
            "", [(key, text) for key, text in _TOOLS], self._tool, self._tool_btns, self._set_tool, compact=True,
        ))
        self._preset_btns = {}
        side.addLayout(self._choice_row(
            "", list(PALETTES.keys()), self._palette_name, self._preset_btns, self._pick_palette, compact=True,
        ))
        side.addStretch(1)
        lay.addLayout(side, 0)

        self.swatches = _SwatchGrid()
        self.swatches.picked.connect(self._pick_swatch)
        lay.addWidget(self.swatches, 1)
        return box

    def _medium(self, text, slot) -> QPushButton:
        btn = QPushButton(text)
        btn.setProperty("kind", "medium")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.clicked.connect(slot)
        return btn

    def _name_edit(self, text, placeholder, slot) -> QLineEdit:
        edit = QLineEdit()
        edit.setObjectName("CalcInput")
        edit.setText(text)
        edit.setPlaceholderText(placeholder)
        edit.setFixedWidth(120)
        edit.editingFinished.connect(slot)
        return edit

    def _dollar_box(self, checked, slot) -> QCheckBox:
        box = QCheckBox("文件名加 $")
        box.setChecked(checked)
        box.stateChanged.connect(slot)
        return box

    def _choice_row(self, title, items, current, store, slot, compact=False) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        if title:
            lab = QLabel(title)
            lab.setFixedWidth(36)
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
            btn.setFixedHeight(28)
            btn.clicked.connect(lambda _=False, v=value: slot(v))
            store[value] = btn
            row.addWidget(btn, 0 if compact else 1)
        self._mark_choice(store, current)
        return row

    def _mark_choice(self, store, current) -> None:
        for value, btn in store.items():
            btn.setChecked(value == current)
        self._style_choices()

    def _qss_choice(self, on: bool) -> str:
        if on:
            return (
                f"QPushButton{{background:{tk('accent')};color:#ffffff;border:1px solid {tk('accent')};"
                f"border-radius:6px;padding:2px 8px;font-size:12px;}}"
            )
        return (
            f"QPushButton{{background:transparent;color:{tk('text')};border:1px solid {tk('border')};"
            f"border-radius:6px;padding:2px 8px;font-size:12px;}}"
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
            getattr(self, "_clip_btns", {}),
        )
        for store in stores:
            for btn in store.values():
                btn.setStyleSheet(self._qss_choice(btn.isChecked()))
        swap = getattr(self, "btn_swap", None)
        if swap is not None:
            swap.setStyleSheet(self._qss_choice(False))
        for lab in self.findChildren(QLabel):
            if str(lab.property("pixelMuted") or "") == "1":
                lab.setStyleSheet(f"color:{tk('text_mut')};background:transparent;font-size:12px;")
        for spec_name in ("lbl_spec", "lbl_art_spec"):
            spec = getattr(self, spec_name, None)
            if spec is not None:
                spec.setStyleSheet(f"color:{tk('text_mut')};background:transparent;font-size:12px;")
        fps = getattr(self, "lbl_fps", None)
        if fps is not None:
            fps.setStyleSheet(f"color:{tk('text')};background:transparent;font-size:12px;")
        hex_lbl = getattr(self, "lbl_hex", None)
        if hex_lbl is not None:
            hex_lbl.setStyleSheet(f"color:{tk('text')};background:transparent;font-size:12px;")

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
        for view in (self.base_view, self.editor, self.preview, self.well_fg, self.well_bg, self.swatches):
            view.update()
        self._style_choices()
        self._sync_art()
        self._sync_walk()
        self._sync_palette()
        self._style_one_status(self.lbl_art_status, self._art_tone)
        self._style_one_status(self.lbl_walk_status, self._walk_tone)

    def _select_art(self, direction: str) -> None:
        if direction not in DIRS:
            return
        self._art_dir = direction
        self._sync_art()

    def _set_tool(self, name: str) -> None:
        keys = [key for key, _text in _TOOLS]
        if name not in keys:
            name = "pencil"
        self._tool = name
        drag = name in ("pencil", "eraser")
        for cell in getattr(self, "_dirs", {}).values():
            cell.view.set_drag_paint(drag)
        for view in (getattr(self, "base_view", None), getattr(self, "editor", None)):
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
        colors = [hex_rgb(item) for item in PALETTES.get(self._palette_name, PALETTES["经典"])]
        self.swatches.set_colors(colors, self._fg, self._bg)
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

    def _pick_palette(self, name: str) -> None:
        if name not in PALETTES:
            return
        self._palette_name = name
        self._sync_palette()

    def _apply_tool(self, im, x: int, y: int, is_bg: bool, sample=None) -> bool:
        if self._tool == "pick":
            src = sample if sample is not None else im
            if x < 0 or y < 0 or x >= src.size[0] or y >= src.size[1]:
                return False
            px = src.getpixel((x, y))
            color = tuple(px)
            if len(color) == 3:
                color = color + (255,)
            else:
                color = color[:4]
            if is_bg:
                self._bg = color
            else:
                self._fg = color
            self._sync_palette()
            return False
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

    def _composite(self, direction: str):
        return composite_layers(self._layers.get(direction) or [], self._art_cell)

    def _show_image(self, view, im) -> None:
        view.set_image(im)
        view.set_hint_visible(not frame_has_pixels(im))

    def _mark_art_selection(self) -> None:
        for direction, cell in self._dirs.items():
            cell.view.set_selected(direction == self._art_dir)
            cell.restyle()

    def _assign_art_cell(self, value) -> None:
        n = snap_cell(value)
        if n == self._art_cell:
            return
        for layers in self._layers.values():
            for layer in layers:
                layer["image"] = resize_cell(layer["image"], n)
        self._art_cell = n

    def _assign_art_colors(self, value) -> None:
        n = snap_colors(value)
        if n and n != self._art_colors:
            for layers in self._layers.values():
                for layer in layers:
                    layer["image"] = quantize_rgba(layer["image"], n)
        self._art_colors = n

    def _assign_walk_cell(self, value) -> None:
        n = snap_cell(value)
        if n == self._walk_cell:
            return
        self._base_im = resize_cell(self._base_im, n)
        for name in _CLIPS:
            self._clips[name] = [resize_cell(im, n) for im in self._clips[name]]
        self._walk_cell = n
        self._frame_sig = None

    def _assign_walk_colors(self, value) -> None:
        n = snap_colors(value)
        if n and n != self._walk_colors:
            self._base_im = quantize_rgba(self._base_im, n)
            for name in _CLIPS:
                self._clips[name] = [quantize_rgba(im, n) for im in self._clips[name]]
        self._walk_colors = n

    def _pick_art_cell(self, value: int) -> None:
        self._assign_art_cell(value)
        self._mark_choice(self._art_cell_btns, self._art_cell)
        self._layer_sig_cache = None
        self._sync_art()
        self._touch()

    def _pick_art_colors(self, value: int) -> None:
        self._assign_art_colors(value)
        self._mark_choice(self._art_color_btns, self._art_colors)
        self._layer_sig_cache = None
        self._sync_art()
        self._touch()

    def _pick_walk_cell(self, value: int) -> None:
        self._assign_walk_cell(value)
        self._mark_choice(self._walk_cell_btns, self._walk_cell)
        self._sync_walk()
        self._touch()

    def _pick_walk_colors(self, value: int) -> None:
        self._assign_walk_colors(value)
        self._mark_choice(self._walk_color_btns, self._walk_colors)
        self._sync_walk()
        self._touch()

    def _on_art_stroke(self, direction: str, x: int, y: int, is_bg: bool) -> None:
        if direction not in self._layers:
            return
        switched = direction != self._art_dir
        if switched:
            self._art_dir = direction
            self._layer_sig_cache = None
        layers = self._layers[direction]
        index = self._active_layer.get(direction, 0)
        if index < 0 or index >= len(layers):
            index = 0
            self._active_layer[direction] = 0
        layer = layers[index]
        if not layer.get("visible", True) and self._tool != "pick":
            self._set_art_status("这一层藏着，先打开再画", "warn")
            if switched:
                self._mark_art_selection()
                self._sync_layers()
            return
        sample = self._composite(direction) if self._tool == "pick" else None
        changed = self._apply_tool(layer["image"], x, y, is_bg, sample)
        if changed or switched:
            self._show_image(self._dirs[direction].view, self._composite(direction))
            self._mark_art_selection()
            if switched:
                self._sync_layers()
        if self._tool == "pick":
            picked = self._bg if is_bg else self._fg
            self._set_art_status(f"已取色 {self._hex_text(picked)}", "ok")

    def _on_base_stroke(self, x: int, y: int, is_bg: bool) -> None:
        changed = self._apply_tool(self._base_im, x, y, is_bg)
        if changed:
            self._show_image(self.base_view, self._base_im)
        elif self._tool == "pick":
            picked = self._bg if is_bg else self._fg
            self._set_walk_status(f"已取色 {self._hex_text(picked)}", "ok")

    def _on_editor_stroke(self, x: int, y: int, is_bg: bool) -> None:
        frames = self._clips[self._clip]
        if self._frame_i < 0 or self._frame_i >= len(frames):
            return
        im = frames[self._frame_i]
        changed = self._apply_tool(im, x, y, is_bg)
        if not changed:
            if self._tool == "pick":
                picked = self._bg if is_bg else self._fg
                self._set_walk_status(f"已取色 {self._hex_text(picked)}", "ok")
            return
        self._show_image(self.editor, im)
        if 0 <= self._frame_i < len(self._frame_cells):
            self._show_image(self._frame_cells[self._frame_i].view, im)
        if not self._timer.isActive():
            self._show_image(self.preview, im)

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
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        name = QPushButton(layer["name"])
        name.setCursor(Qt.PointingHandCursor)
        name.setFocusPolicy(Qt.NoFocus)
        name.setFixedHeight(24)
        name.setStyleSheet(self._qss_choice(active))
        name.clicked.connect(lambda _=False, i=index: self._select_layer(i))
        eye = QPushButton("显" if layer.get("visible", True) else "藏")
        eye.setCursor(Qt.PointingHandCursor)
        eye.setFocusPolicy(Qt.NoFocus)
        eye.setFixedSize(32, 24)
        eye.setStyleSheet(self._qss_choice(False))
        eye.clicked.connect(lambda _=False, i=index, b=eye: self._toggle_eye(i, b))
        lay.addWidget(name, 1)
        lay.addWidget(eye, 0)
        if index > 0:
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 100)
            slider.setValue(int(round(float(layer.get("opacity", 1)) * 100)))
            slider.setFixedWidth(78)
            slider.valueChanged.connect(lambda value, i=index: self._on_opacity(i, value))
            lay.addWidget(slider, 0)
        return row

    def _sync_layers(self) -> None:
        sig = self._layer_signature()
        if sig == self._layer_sig_cache and self.layer_rows.count():
            return
        self._layer_sig_cache = sig
        self._clear_layout(self.layer_rows)
        layers = self._layers[self._art_dir]
        active = self._active_layer.get(self._art_dir, 0)
        self.lbl_layers.setText(f"分层·{DIR_LABEL[self._art_dir]}")
        for index in range(len(layers) - 1, -1, -1):
            self.layer_rows.addWidget(self._layer_row(index, layers[index], index == active))

    def _select_layer(self, index: int) -> None:
        layers = self._layers[self._art_dir]
        if index < 0 or index >= len(layers):
            return
        self._active_layer[self._art_dir] = index
        self._layer_sig_cache = None
        self._sync_layers()

    def _toggle_eye(self, index: int, btn) -> None:
        layers = self._layers[self._art_dir]
        if index < 0 or index >= len(layers):
            return
        layer = layers[index]
        layer["visible"] = not layer.get("visible", True)
        btn.setText("显" if layer["visible"] else "藏")
        self._show_image(self._dirs[self._art_dir].view, self._composite(self._art_dir))

    def _on_opacity(self, index: int, value: int) -> None:
        layers = self._layers[self._art_dir]
        if index <= 0 or index >= len(layers):
            return
        layers[index]["opacity"] = max(0.0, min(1.0, int(value) / 100.0))
        self._show_image(self._dirs[self._art_dir].view, self._composite(self._art_dir))

    def _add_layer(self) -> None:
        layers = self._layers[self._art_dir]
        if len(layers) >= _MAX_LAYERS:
            self._set_art_status("一层画板最多 6 层", "warn")
            return
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
        self.lbl_fps.setText(f"{self._fps} 帧/秒")
        if self._timer.isActive():
            self._timer.setInterval(self._interval())
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
        self._sync_layers()

    def _stop_play(self) -> None:
        self._timer.stop()
        if getattr(self, "btn_play", None) is not None:
            self.btn_play.setText("播放")

    def _select_clip(self, name: str) -> None:
        if name not in self._clips:
            return
        if name != self._clip:
            self._clip = name
            self._frame_i = 0
            self._stop_play()
            self._frame_sig = None
            self._sync_walk()
            return
        self._mark_choice(self._clip_btns, self._clip)

    def _select_frame(self, index: int) -> None:
        frames = self._clips[self._clip]
        if index < 0 or index >= len(frames):
            return
        self._frame_i = index
        self._sync_walk()

    def _rebuild_frame_row(self) -> None:
        self._clear_layout(self.frame_row)
        self._frame_cells = []
        frames = self._clips[self._clip]
        for index in range(len(frames)):
            cell = _FrameCell(index)
            cell.clicked.connect(self._select_frame)
            cell.dropped.connect(self._ingest_frame)
            cell.lbl.setText(f"{index + 1:02d}")
            self._frame_cells.append(cell)
            self.frame_row.addWidget(cell, 1)

    def _sync_walk(self) -> None:
        frames = self._clips[self._clip]
        if not frames:
            frames.append(blank_cell(self._walk_cell))
        if self._frame_i >= len(frames):
            self._frame_i = len(frames) - 1
        if self._frame_i < 0:
            self._frame_i = 0
        sig = (self._clip, len(frames))
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
            cell.lbl.setText(f"{cell.index + 1:02d}")
            cell.restyle()
        if not self._timer.isActive():
            self._show_image(self.preview, current)
        self.lbl_fps.setText(f"{self._fps} 帧/秒")
        self._mark_choice(self._clip_btns, self._clip)

    def _copy_base(self) -> None:
        frames = self._clips[self._clip]
        frames[self._frame_i] = self._base_im.copy()
        self._stop_play()
        self._sync_walk()
        self._set_walk_status(f"已把底图复制到 {self._clip} {self._frame_i + 1:02d}", "ok")

    def _add_frame(self) -> None:
        frames = self._clips[self._clip]
        if len(frames) >= _MAX_FRAMES:
            self._set_walk_status("一条最多 8 帧", "warn")
            return
        self._stop_play()
        frames.append(blank_cell(self._walk_cell))
        self._frame_i = len(frames) - 1
        self._frame_sig = None
        self._sync_walk()
        self._set_walk_status(f"已加上 {self._frame_i + 1:02d} 帧", "ok")

    def _dup_frame(self) -> None:
        frames = self._clips[self._clip]
        if len(frames) >= _MAX_FRAMES:
            self._set_walk_status("一条最多 8 帧", "warn")
            return
        self._stop_play()
        frames.insert(self._frame_i + 1, frames[self._frame_i].copy())
        self._frame_i += 1
        self._frame_sig = None
        self._sync_walk()
        self._set_walk_status(f"已复制出 {self._frame_i + 1:02d} 帧", "ok")

    def _del_frame(self) -> None:
        frames = self._clips[self._clip]
        if len(frames) <= 1:
            self._set_walk_status("至少留一帧", "warn")
            return
        self._stop_play()
        del frames[self._frame_i]
        if self._frame_i >= len(frames):
            self._frame_i = len(frames) - 1
        self._frame_sig = None
        self._sync_walk()
        self._set_walk_status(f"当前是 {self._clip} {self._frame_i + 1:02d}", "ok")

    def _browse_art(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "打开图片", "", _OPEN_FILTER)
        if path:
            self._ingest_art(self._art_dir, path)

    def _browse_walk(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "打开图片", "", _OPEN_FILTER)
        if path:
            self._ingest_base(path)

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
        note = ""
        if not has_transparency(im):
            note = "。这张图没有透明底"
        prepared = prepare_cell(im, self._art_cell, self._art_colors)
        self._art_dir = direction
        layers = self._layers[direction]
        index = self._active_layer.get(direction, 0)
        if index < 0 or index >= len(layers):
            index = 0
            self._active_layer[direction] = 0
        layers[index]["image"] = prepared
        self._layer_sig_cache = None
        self._sync_art()
        tone = "warn" if note else "ok"
        self._set_art_status(f"已放入「{DIR_LABEL[direction]}」的{layers[index]['name']}{note}", tone)

    def _ingest_base(self, path: str) -> None:
        im = self._load_dropped(path, self._set_walk_status)
        if im is None:
            return
        note = ""
        if not has_transparency(im):
            note = "。这张图没有透明底"
        self._base_im = prepare_cell(im, self._walk_cell, self._walk_colors)
        self._sync_walk()
        self._set_walk_status(f"已放入底图{note}", "warn" if note else "ok")

    def _ingest_frame(self, index: int, path: str) -> None:
        im = self._load_dropped(path, self._set_walk_status)
        if im is None:
            return
        frames = self._clips[self._clip]
        if index < 0 or index >= len(frames):
            return
        frames[index] = prepare_cell(im, self._walk_cell, self._walk_colors)
        self._frame_i = index
        self._stop_play()
        self._sync_walk()
        self._set_walk_status(f"已换掉 {self._clip} {index + 1:02d}", "ok")

    def _mirror(self) -> None:
        src_layers = self._layers.get("right") or []
        if not any(frame_has_pixels(layer.get("image")) for layer in src_layers):
            self._set_art_status("先在右边画一点，或拖进一张图", "warn")
            return
        copied = []
        for layer in src_layers:
            copied.append({
                "name": layer["name"],
                "image": layer["image"].transpose(Image.FLIP_LEFT_RIGHT),
                "opacity": layer.get("opacity", 1),
                "visible": layer.get("visible", True),
            })
        self._layers["left"] = copied
        self._active_layer["left"] = min(self._active_layer.get("right", 0), len(copied) - 1)
        self._art_dir = "left"
        self._layer_sig_cache = None
        self._sync_art()
        self._set_art_status("已把右镜像到左", "ok")

    def _interval(self) -> int:
        return max(40, int(round(1000 / max(1, self._fps))))

    def _toggle_play(self) -> None:
        if self._timer.isActive():
            self._stop_play()
            return
        frames = self._clips[self._clip]
        if not any(frame_has_pixels(fr) for fr in frames):
            self._set_walk_status("这条还没有图", "warn")
            return
        self._play_at = 0
        self._show_image(self.preview, frames[0])
        self.btn_play.setText("停止")
        self._timer.start(self._interval())

    def _on_tick(self) -> None:
        frames = self._clips[self._clip]
        if not frames:
            self._stop_play()
            return
        self._play_at = (self._play_at + 1) % len(frames)
        self._show_image(self.preview, frames[self._play_at])

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

    def _clips_have_pixels(self) -> bool:
        for frames in self._clips.values():
            if any(frame_has_pixels(fr) for fr in frames):
                return True
        return False

    def _export_art_sheet(self) -> None:
        if not self._art_has_pixels():
            self._set_art_status("还没有图", "warn")
            return
        folder = self._dest_dir(self._set_art_status)
        if not folder:
            return
        path = os.path.join(folder, self._art_stem() + ".png")
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
        try:
            count = 0
            for direction in DIRS:
                im = self._composite(direction)
                if not frame_has_pixels(im):
                    continue
                im.save(os.path.join(folder, f"{stem}_{DIR_LABEL[direction]}.png"), "PNG")
                count += 1
        except Exception:
            log.exception("像素画板保存四向失败")
            self._set_art_status("四向没有写成", "err")
            return
        self._set_art_status(f"已保存 {count} 张站立图", "ok")

    def _export_walk_sheet(self) -> None:
        if not self._clips_have_pixels():
            self._set_walk_status("还没有图", "warn")
            return
        folder = self._dest_dir(self._set_walk_status)
        if not folder:
            return
        path = os.path.join(folder, self._walk_stem() + ".png")
        try:
            rows = [self._clips[name] for name in _CLIPS]
            compose_clip_sheet(rows, self._walk_cell).save(path, "PNG")
        except Exception:
            log.exception("像素画板保存行走整表失败")
            self._set_walk_status("整表没有写成", "err")
            return
        self._set_walk_status(f"已保存整表：{os.path.basename(path)}", "ok")

    def _export_gif(self) -> None:
        frames = list(self._clips[self._clip])
        if not any(frame_has_pixels(fr) for fr in frames):
            self._set_walk_status("这条还没有图", "warn")
            return
        folder = self._dest_dir(self._set_walk_status)
        if not folder:
            return
        path = os.path.join(folder, f"{self._walk_stem()}_{self._clip}.gif")
        try:
            write_gif(frames, path, self._interval())
        except Exception:
            log.exception("像素画板保存 GIF 失败")
            self._set_walk_status("GIF 没有写成", "err")
            return
        self._set_walk_status(f"已保存 GIF：{os.path.basename(path)}", "ok")

    def _export_frames(self) -> None:
        if not self._clips_have_pixels():
            self._set_walk_status("还没有图", "warn")
            return
        folder = self._dest_dir(self._set_walk_status)
        if not folder:
            return
        out_dir = os.path.join(folder, self._walk_stem() + "_逐帧")
        try:
            os.makedirs(out_dir, exist_ok=True)
            count = 0
            for name in _CLIPS:
                for index, fr in enumerate(self._clips[name]):
                    if not frame_has_pixels(fr):
                        continue
                    fr.save(os.path.join(out_dir, f"{name}_{index + 1:02d}.png"), "PNG")
                    count += 1
        except Exception:
            log.exception("像素画板保存逐帧失败")
            self._set_walk_status("逐帧没有写成", "err")
            return
        self._set_walk_status(f"已保存 {count} 张逐帧", "ok")

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

    def apply_settings(self, data: dict) -> None:
        data = data or {}
        self._loading = True
        try:
            self._save_path = str(data.get("save_path") or "").replace("\\", "/")
            self._assign_art_cell(data.get("art_cell", data.get("cell", 48)))
            self._assign_art_colors(data.get("art_colors", data.get("colors", 0)))
            self._assign_walk_cell(data.get("cell", 48))
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
            self._walk_name = str(data.get("name") or "角色")
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
            self._mark_choice(self._art_cell_btns, self._art_cell)
            self._mark_choice(self._art_color_btns, self._art_colors)
            self._mark_choice(self._walk_cell_btns, self._walk_cell)
            self._mark_choice(self._walk_color_btns, self._walk_colors)
            self._layer_sig_cache = None
            self._frame_sig = None
            self._sync_art()
            self._sync_walk()
            self._sync_palette()
        finally:
            self._loading = False

    def export_settings(self) -> dict:
        return {
            "save_path": self._save_path,
            "cell": self._walk_cell,
            "colors": self._walk_colors,
            "stride": self._keep_stride,
            "leg": self._keep_leg,
            "fps": self._fps,
            "four": self._keep_four,
            "dollar": self.chk_walk_dollar.isChecked(),
            "name": self.edit_walk_name.text() or "角色",
            "art_cell": self._art_cell,
            "art_colors": self._art_colors,
            "art_dollar": self.chk_art_dollar.isChecked(),
            "art_name": self.edit_art_name.text() or "画板",
        }
