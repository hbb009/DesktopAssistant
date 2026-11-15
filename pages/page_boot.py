# pages/page_boot.py — 开机员：目录、网站、程序，以及本机启动项
from __future__ import annotations

import os
import uuid

from PyQt5.QtCore import Qt, QEvent, QSize, QUrl, QFileInfo, QPoint, QRect, QMimeData, QTimer
from PyQt5.QtGui import QDesktopServices, QFont, QFontMetrics, QPixmap, QDrag, QPainter, QColor, QCursor
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QFrame, QFileDialog, QInputDialog,
    QFileIconProvider, QMessageBox, QAbstractButton, QScrollBar,
    QGraphicsOpacityEffect,
)

from styles.style_all import (
    apply_transparent_surface, apply_medium_button, apply_mini_button,
    make_card, install_card_title, theme, tk,
    CARD_TITLE_BODY_GAP, CARD_BOTTOM_GAP,
    message_box_warn,
)
from utils.flow_layout import FlowLayout
from utils.logger import get_logger
from utils import boot_desk

log = get_logger(__name__)

_CARD_H = 46
_DIR_CARD_H = _CARD_H // 2 + 6
_LIST_GAP = 4
_APP_W = 72
_APP_ICON = 34
_APP_NAME_PX = 12
_DIR_ICON = 22
_FOLDER_ICON = 16
_SITE_ICON = max(12, int(round(_DIR_ICON * 0.7)))
_HINT = "单击加选，再点减选，双击打开，拖动可调整顺序。目录和程序可以直接拖进来。"
MIME_BOOT_CARD = "application/x-boot-card"
DRAG_THRESHOLD = 8
_MONOGRAM = (
    "#3B82F6", "#8B5CF6", "#EC4899", "#F97316",
    "#14B8A6", "#22C55E", "#EAB308", "#6366F1",
)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _boot_mime(mime):
    """拖拽排序的载荷：分组 + 卡片 id。外部文件拖入没有这个格式。"""
    if mime is None or not mime.hasFormat(MIME_BOOT_CARD):
        return "", ""
    try:
        raw = bytes(mime.data(MIME_BOOT_CARD)).decode("utf-8")
    except Exception:
        return "", ""
    kind, sep, iid = raw.partition("\t")
    if not sep or kind not in ("dirs", "sites", "apps") or not iid:
        return "", ""
    return kind, iid


def _reorder_list(items, src_id, target_id, insert_after: bool):
    """把 src 插到 target 前或后。target_id 为空表示放到末尾。顺序没变则 changed 为 False。"""
    src = next((x for x in items if x.get("id") == src_id), None)
    if src is None or src_id == target_id:
        return list(items), False
    rest = [x for x in items if x.get("id") != src_id]
    if not target_id:
        rest.append(src)
    else:
        idx = next((i for i, x in enumerate(rest) if x.get("id") == target_id), None)
        if idx is None:
            rest.append(src)
        else:
            rest.insert(idx + (1 if insert_after else 0), src)
    changed = [x.get("id") for x in rest] != [x.get("id") for x in items]
    return rest, changed


def _pass_clicks(widget):
    """文字和图标不吃点击，整张卡片都能打开。关闭按钮除外。"""
    widget.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    return widget


def _hue(text: str) -> str:
    h = 0
    for ch in text or "":
        h = (h * 33 + ord(ch)) & 0xFFFFFFFF
    return _MONOGRAM[h % len(_MONOGRAM)]


class _LineHost(QWidget):
    """卡片容器。拖拽改序时在落点画一条插入线。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drop_line = None
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    def set_drop_line(self, rect):
        self._drop_line = rect
        self.update()

    def clear_drop_line(self):
        if self._drop_line is not None:
            self._drop_line = None
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        rect = self._drop_line
        if rect is None or rect.width() <= 0 or rect.height() <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(rect, QColor(tk("text")))
        painter.end()


class _FlowHost(_LineHost):
    """流式图标区。宽度跟着父级走，不能把自己的宽度报成首选宽度，否则滚动条会来回出现。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        lay = self.layout()
        if lay is not None and lay.hasHeightForWidth():
            return lay.heightForWidth(max(1, w))
        return super().heightForWidth(w)

    def sizeHint(self):
        return QSize(0, max(1, self.heightForWidth(max(self.width(), 320))))

    def minimumSizeHint(self):
        return QSize(0, 0)


class _ElideLabel(QLabel):
    """中间省略。水平尺寸固定为 0，避免路径变长把右边撑出滚动条。"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = text or ""
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(self._full)

    def sizeHint(self):
        return QSize(0, QFontMetrics(self.font()).height() + 2)

    def minimumSizeHint(self):
        return self.sizeHint()

    def set_full(self, text: str):
        self._full = text or ""
        self._apply()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply()

    def showEvent(self, event):
        super().showEvent(event)
        self._apply()

    def _apply(self):
        w = self.width()
        if w < 12:
            return
        shown = QFontMetrics(self.font()).elidedText(self._full, Qt.ElideMiddle, max(1, w - 2))
        if shown != self.text():
            self.setText(shown)


def _site_name_from_url(url: str) -> str:
    host = (url or "").split("://", 1)[-1].split("/")[0].split("?")[0]
    if host.lower().startswith("www."):
        host = host[4:]
    return host or url


def _is_program_file(path: str) -> bool:
    if not path or not os.path.isfile(path):
        return False
    return os.path.splitext(path)[1].lower() in (".exe", ".lnk")


class _PixBox(QWidget):
    """把整张图标画进固定方格。外壳图标经常比格子大，直接塞进 QLabel 会切掉右边。"""

    def __init__(self, pix: QPixmap, side: int, parent=None):
        super().__init__(parent)
        self._pix = pix if pix is not None else QPixmap()
        self.setFixedSize(side, side)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def paintEvent(self, _event):
        if self._pix.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawPixmap(self.rect(), self._pix)
        painter.end()


class _BootItem(QFrame):
    """单击加选，再点已选中的卡减选，双击打开，拖动改本组顺序。"""

    def __init__(self, page, kind, item_id, on_pick, on_open, parent=None):
        super().__init__(parent)
        self._page = page
        self._kind = kind
        self._item_id = item_id or ""
        self._on_pick = on_pick
        self._on_open = on_open
        self._press_pos = None
        self._dragging = False
        self._toggle_off_on_release = False
        self._frozen = False
        self.setAcceptDrops(True)
        self.setCursor(Qt.OpenHandCursor)

    def set_frozen(self, frozen: bool):
        """这台电脑上没有对应目录或程序时，卡片不响应点击和拖动。"""
        self._frozen = bool(frozen)
        self.setProperty("bootFrozen", self._frozen)
        self.setCursor(Qt.ArrowCursor if self._frozen else Qt.OpenHandCursor)
        if self._frozen:
            effect = QGraphicsOpacityEffect(self)
            effect.setOpacity(0.4)
            self.setGraphicsEffect(effect)
        else:
            self.setGraphicsEffect(None)

    def _selected_now(self) -> bool:
        page = self._page
        if page is None:
            return False
        key = (self._kind, str(self._item_id or ""))
        return key in getattr(page, "_sel", [])

    def mousePressEvent(self, event):
        if self._frozen:
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            self._press_pos = event.pos()
            self._dragging = False
            self.setCursor(Qt.ClosedHandCursor)
            # 已选中的卡等松开再减选，避免按住拖动排序时把选中弄掉。
            self._toggle_off_on_release = self._selected_now()
            if not self._toggle_off_on_release and callable(self._on_pick):
                self._on_pick()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._frozen:
            return
        if self._dragging or self._press_pos is None or not (event.buttons() & Qt.LeftButton):
            return
        if (event.pos() - self._press_pos).manhattanLength() < DRAG_THRESHOLD:
            return
        self._dragging = True
        self._start_drag()

    def mouseReleaseEvent(self, event):
        if self._frozen:
            event.accept()
            return
        self.setCursor(Qt.OpenHandCursor)
        toggle = self._toggle_off_on_release and not self._dragging
        self._press_pos = None
        self._toggle_off_on_release = False
        if toggle and callable(self._on_pick):
            self._on_pick()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self._frozen:
            page = self._page
            if page is not None and event.button() == Qt.LeftButton:
                page._set_status("这台电脑上没有这项。", ok=False)
            event.accept()
            return
        if event.button() == Qt.LeftButton and not self._dragging and callable(self._on_open):
            self._on_open()
        super().mouseDoubleClickEvent(event)

    def _start_drag(self):
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(MIME_BOOT_CARD, ("%s\t%s" % (self._kind, self._item_id)).encode("utf-8"))
        drag.setMimeData(mime)
        pix = QPixmap(self.size())
        pix.fill(Qt.transparent)
        self.render(pix)
        painter = QPainter(pix)
        painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        painter.fillRect(pix.rect(), QColor(0, 0, 0, 170))
        painter.end()
        drag.setPixmap(pix)
        hot = self._press_pos if self._press_pos is not None else QPoint(self.width() // 2, self.height() // 2)
        drag.setHotSpot(hot)
        drag.exec_(Qt.MoveAction)
        self._dragging = True
        page = self._page
        if page is not None:
            page._clear_reorder_lines()


class _PickCard(_BootItem):
    def __init__(self, page, kind, item_id, on_pick, on_open, height=None, parent=None):
        super().__init__(page, kind, item_id, on_pick, on_open, parent)
        if height:
            self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)


class _AppTile(_BootItem):
    def __init__(self, page, kind, item_id, on_pick, on_open, parent=None):
        super().__init__(page, kind, item_id, on_pick, on_open, parent)
        self.setFixedWidth(_APP_W)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)


class PageBoot(QWidget):
    def __init__(self):
        super().__init__()
        apply_transparent_surface(self, "PageBoot")
        self._dirty = None
        self._dirs = []
        self._sites = []
        self._apps = []
        self._icons = QFileIconProvider()
        self._sel = []  # [(kind, id), ...] 未选中再点是加选，已选中再点是减选，点空白清空
        self._sel_undo = []  # 空白误清掉的多选，点「恢复选中」前暂时留着
        self._reorder_rebuild_kind = ""
        self._reorder_rebuild_queued = False

        lay = QVBoxLayout(self)
        # ContentRoot 已经有内边距。这里再加一层，会比其它页松一圈。
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.btn_all = apply_medium_button(QPushButton("全部打开"))
        self.btn_all.clicked.connect(self._open_all)
        self.btn_open_sel = QPushButton("打开选中")
        self.btn_open_sel.setObjectName("BootOpenSel")
        self.btn_open_sel.setCursor(Qt.PointingHandCursor)
        self.btn_open_sel.setStyleSheet(
            "QPushButton#BootOpenSel{background:#3a8ee0;border:1px solid #2f7cc8;color:#ffffff;"
            "padding:0 12px;min-height:27px;max-height:27px;min-width:0px;"
            "font-size:13px;font-weight:600;border-radius:7px;}"
            "QPushButton#BootOpenSel:hover{background:#4f9de8;border-color:#7cbcf2;}"
            "QPushButton#BootOpenSel:pressed{background:#2f7cc8;border-color:#2a6fb5;}"
            "QPushButton#BootOpenSel:disabled{background:rgba(58,142,224,0.30);"
            "border-color:rgba(90,166,238,0.28);color:rgba(255,255,255,0.55);}"
        )
        self.btn_open_sel.clicked.connect(self._on_open_sel_clicked)
        self.btn_rename = apply_medium_button(QPushButton("改名"))
        self.btn_rename.clicked.connect(self._rename_selected)
        self.btn_delete = apply_medium_button(QPushButton("删除"))
        self.btn_delete.clicked.connect(self._delete_selected)
        self.btn_startup = apply_medium_button(QPushButton("启动项"))
        self.btn_startup.clicked.connect(self._open_settings)
        bar.addWidget(self.btn_all, 0)
        bar.addWidget(self.btn_open_sel, 0)
        bar.addWidget(self.btn_rename, 0)
        bar.addWidget(self.btn_delete, 0)
        self.lbl_status = _ElideLabel(_HINT)
        self.lbl_status.set_full(_HINT)
        bar.addWidget(self.lbl_status, 1)
        bar.addWidget(self.btn_startup, 0)
        lay.addLayout(bar)

        cols = QHBoxLayout()
        cols.setSpacing(6)
        self.card_dirs = self._make_section(
            "BootDirs", "目录", self._add_dir, lambda: self._open_group("dirs"), "dirs",
        )
        self.card_sites = self._make_section(
            "BootSites", "网站", self._add_site, lambda: self._open_group("sites"), "sites",
        )
        cols.addWidget(self.card_dirs["card"], 1)
        cols.addWidget(self.card_sites["card"], 1)
        lay.addLayout(cols, 3)

        self.card_apps = self._make_section(
            "BootApps", "程序", self._add_app, lambda: self._open_group("apps"),
            "apps", flow=True,
        )
        lay.addWidget(self.card_apps["card"], 2)

        theme.changed.connect(self.restyle_theme)
        self.restyle_theme()
        self._rebuild_dirs()
        self._rebuild_sites()
        self._rebuild_apps()
        self._watch_clicks(self)
        self._apply_pick_style()

    def set_prefs_dirty_callback(self, fn):
        self._dirty = fn

    def _mark(self):
        fn = self._dirty
        if callable(fn):
            fn()

    def export_settings(self) -> dict:
        return {
            "dirs": [dict(x) for x in self._dirs],
            "sites": [dict(x) for x in self._sites],
            "apps": [dict(x) for x in self._apps],
        }

    def apply_settings(self, data: dict):
        data = data if isinstance(data, dict) else {}
        self._dirs = self._clean_dirs(data.get("dirs"))
        self._sites = self._clean_sites(data.get("sites"))
        self._apps = self._clean_apps(data.get("apps"))
        self._rebuild_dirs()
        self._rebuild_sites()
        self._rebuild_apps()

    def restyle_theme(self, *_):
        self.lbl_status.setStyleSheet(self._label_qss(tk("text")))
        self._rebuild_dirs()
        self._rebuild_sites()
        self._rebuild_apps()

    # ── 数据整理 ──────────────────────────────────────────────

    @staticmethod
    def _clean_dirs(raw) -> list:
        out = []
        seen = set()
        if not isinstance(raw, list):
            return out
        for item in raw:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            key = os.path.normcase(os.path.abspath(path))
            if key in seen:
                continue
            seen.add(key)
            name = str(item.get("name") or "").strip() or os.path.basename(path) or path
            out.append({"id": str(item.get("id") or _new_id()), "name": name, "path": path})
        return out

    @staticmethod
    def _clean_sites(raw) -> list:
        out = []
        seen = set()
        if not isinstance(raw, list):
            return out
        for item in raw:
            if not isinstance(item, dict):
                continue
            url = boot_desk.normalize_url(str(item.get("url") or ""))
            if not url:
                continue
            key = url.rstrip("/").lower()
            if key in seen:
                continue
            seen.add(key)
            name = str(item.get("name") or "").strip() or url
            out.append({"id": str(item.get("id") or _new_id()), "name": name, "url": url})
        return out

    @staticmethod
    def _clean_apps(raw) -> list:
        out = []
        seen = set()
        if not isinstance(raw, list):
            return out
        for item in raw:
            if not isinstance(item, dict):
                continue
            lnk = boot_desk.safe_lnk_basename(str(item.get("lnk") or ""))
            if not lnk or lnk.lower() in seen:
                continue
            seen.add(lnk.lower())
            name = str(item.get("name") or "").strip() or os.path.splitext(lnk)[0]
            target = str(item.get("target") or "").strip()
            out.append({
                "id": str(item.get("id") or _new_id()),
                "name": name,
                "lnk": lnk,
                "target": target,
            })
        return out

    # ── 外观 ──────────────────────────────────────────────────

    def _make_section(self, object_name: str, title: str, on_add, on_open, kind: str, flow: bool = False) -> dict:
        card = make_card(object_name)
        card.setProperty("bootSection", kind)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay = QVBoxLayout(card)
        lay.setSpacing(CARD_TITLE_BODY_GAP)
        lay.setContentsMargins(12, 8, 12, CARD_BOTTOM_GAP)
        title_lbl = install_card_title(card, lay, title)
        head = title_lbl.parentWidget()
        # 标题条原来的样式没有选择器，会套到里面的按钮上，把边框和底色清掉。
        head.setStyleSheet("#CardTitleHead{background:transparent;border:none;}")
        head_lay = head.layout()
        head_lay.removeWidget(title_lbl)
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(6)
        btn_add = apply_mini_button(QPushButton("添加"))
        btn_open = apply_mini_button(QPushButton("打开本组"))
        btn_add.clicked.connect(on_add)
        btn_open.clicked.connect(on_open)
        bar.addWidget(title_lbl, 0, Qt.AlignVCenter)
        bar.addStretch(1)
        bar.addWidget(btn_add, 0, Qt.AlignVCenter)
        bar.addWidget(btn_open, 0, Qt.AlignVCenter)
        head_lay.addLayout(bar)
        scroll = QScrollArea()
        scroll_name = object_name + "Scroll"
        apply_transparent_surface(scroll, scroll_name)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        if flow:
            host = _FlowHost()
            apply_transparent_surface(host, object_name + "Flow")
            box = FlowLayout(host, margin=0, h_spacing=8, v_spacing=10)
        else:
            host = _LineHost()
            apply_transparent_surface(host, object_name + "List")
            host.setMinimumWidth(0)
            host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
            box = QVBoxLayout(host)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(_LIST_GAP)
        host.setProperty("bootFlow", bool(flow))
        scroll.setWidget(host)
        lay.addWidget(scroll, 1)
        return {"card": card, "box": box, "host": host}

    @staticmethod
    def _label_qss(color: str) -> str:
        return "background: transparent; border: none; color: %s;" % color

    def _item_qss(self, object_name: str, picked: bool = False, frozen: bool = False) -> str:
        if frozen:
            border = tk("border")
            hover = border
        else:
            border = tk("accent") if picked else tk("border")
            hover = tk("accent")
        return (
            "QFrame#%s{background:%s;border:1px solid %s;border-radius:8px;}"
            "QFrame#%s:hover{border:1px solid %s;}"
        ) % (object_name, tk("panel_2"), border, object_name, hover)

    def _item_ready(self, kind: str, item: dict) -> bool:
        """网站不依赖这台电脑。目录和程序对不上时，卡片冻结。"""
        if kind == "dirs":
            path = item.get("path") or ""
            return bool(path) and os.path.isdir(path)
        if kind == "apps":
            target = (item.get("target") or "").strip()
            if target and os.path.isfile(target):
                return True
            # 没记下目标时，再看快捷方式指向的程序还在不在。副本本身不算程序。
            if target:
                return False
            lnk = boot_desk.shortcut_path(item.get("lnk") or "")
            if not lnk or not os.path.isfile(lnk):
                return False
            resolved = boot_desk.read_shortcut_target(lnk)
            return bool(resolved) and os.path.isfile(resolved)
        return True

    def _clear(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _pix(self, path: str, px: int) -> QPixmap:
        if not path or not os.path.exists(path):
            return QPixmap()
        try:
            icon = self._icons.icon(QFileInfo(path))
        except Exception:
            return QPixmap()
        if icon.isNull():
            return QPixmap()
        dpr = 1.0
        try:
            dpr = float(self.devicePixelRatioF() or 1.0)
        except Exception:
            dpr = 1.0
        if dpr < 1.0:
            dpr = 1.0
        side = max(1, int(round(px * dpr)))
        pix = icon.pixmap(QSize(max(side, 32), max(side, 32)))
        if pix.isNull():
            return QPixmap()
        if pix.width() != side or pix.height() != side or abs((pix.devicePixelRatioF() or 1.0) - dpr) > 0.01:
            pix = pix.scaled(side, side, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pix.setDevicePixelRatio(dpr)
        return pix

    def _empty_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setMinimumWidth(0)
        lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lbl.setStyleSheet(self._label_qss(tk("text_dim")))
        return lbl

    def _rebuild_dirs(self):
        box = self.card_dirs["box"]
        self._clear(box)
        if not self._dirs:
            box.addWidget(self._empty_label("还没有目录。点「添加」，或把文件夹拖进来。"))
        else:
            for item in self._dirs:
                box.addWidget(self._dir_card(item))
        box.addStretch(1)
        self._arm_drop_tree(self.card_dirs["card"], "dirs")
        self._apply_pick_style()

    def _rebuild_sites(self):
        box = self.card_sites["box"]
        self._clear(box)
        if not self._sites:
            box.addWidget(self._empty_label("还没有网站。点「添加」填写网址，名称可以以后再改。"))
        else:
            for item in self._sites:
                box.addWidget(self._site_card(item))
        box.addStretch(1)
        self._arm_tree(self.card_sites["card"])
        self._apply_pick_style()

    def _rebuild_apps(self):
        box = self.card_apps["box"]
        self._clear(box)
        host = self.card_apps["host"]
        if not self._apps:
            box.addWidget(self._empty_label("还没有程序。点「添加」，或把 exe、快捷方式拖进来。"))
            host.updateGeometry()
            self._arm_drop_tree(self.card_apps["card"], "apps")
            self._apply_pick_style()
            return
        for item in self._apps:
            box.addWidget(self._app_tile(item))
        host.updateGeometry()
        self._arm_drop_tree(self.card_apps["card"], "apps")
        self._apply_pick_style()

    def _dir_card(self, item: dict) -> QFrame:
        card = _PickCard(
            self, "dirs", item.get("id") or "",
            lambda it=item: self._pick("dirs", it),
            lambda it=item: self._open_dir(it),
            height=_DIR_CARD_H,
        )
        card.setObjectName("BootDirCard")
        card.setProperty("bootKind", "dirs")
        card.setProperty("bootId", item.get("id") or "")
        card.setAttribute(Qt.WA_StyledBackground, True)
        frozen = not self._item_ready("dirs", item)
        card.setStyleSheet(self._item_qss("BootDirCard", frozen=frozen))
        card.set_frozen(frozen)
        row = QHBoxLayout(card)
        row.setContentsMargins(6, 0, 8, 0)
        row.setSpacing(6)
        icon = _PixBox(self._pix(item.get("path") or "", _FOLDER_ICON), _FOLDER_ICON)
        row.addWidget(icon, 0, Qt.AlignVCenter)
        row.addLayout(self._one_line(item.get("name") or "", item.get("path") or ""), 1)
        return card

    def _site_card(self, item: dict) -> QFrame:
        card = _PickCard(
            self, "sites", item.get("id") or "",
            lambda it=item: self._pick("sites", it),
            lambda it=item: self._open_site(it),
        )
        card.setObjectName("BootSiteCard")
        card.setProperty("bootKind", "sites")
        card.setProperty("bootId", item.get("id") or "")
        card.setAttribute(Qt.WA_StyledBackground, True)
        card.setStyleSheet(self._item_qss("BootSiteCard"))
        row = QHBoxLayout(card)
        row.setContentsMargins(6, 6, 8, 6)
        row.setSpacing(6)
        letter = (item.get("name") or item.get("url") or "?").strip()[:1] or "?"
        badge = _pass_clicks(QLabel(letter))
        badge.setFixedSize(_SITE_ICON, _SITE_ICON)
        badge.setAlignment(Qt.AlignCenter)
        font = badge.font()
        font.setBold(True)
        font.setPixelSize(max(9, _SITE_ICON - 4))
        badge.setFont(font)
        badge.setStyleSheet(
            "background:%s;color:#ffffff;border:none;border-radius:3px;font-size:%dpx;"
            % (_hue(item.get("url") or letter), font.pixelSize())
        )
        row.addWidget(badge, 0, Qt.AlignVCenter)
        row.addLayout(self._one_line(item.get("name") or "", item.get("url") or ""), 1)
        return card

    def _one_line(self, primary: str, secondary: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        name = _pass_clicks(_ElideLabel(primary))
        name.set_full(primary)
        font = QFont("Microsoft YaHei")
        font.setPixelSize(14)
        font.setBold(True)
        name.setFont(font)
        name.setStyleSheet(self._label_qss(tk("text")) + "font-size:14px;font-weight:700;")
        # Ignored 会让布局不认固定宽度，名字和路径会叠在一起。
        name.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        name.setFixedWidth(min(QFontMetrics(font).horizontalAdvance(primary) + 10, 168))
        extra = _pass_clicks(_ElideLabel(secondary))
        extra.set_full(secondary)
        extra.setStyleSheet(self._label_qss(tk("text_mut")))
        row.addWidget(name, 0, Qt.AlignVCenter)
        row.addWidget(extra, 1, Qt.AlignVCenter)
        return row

    def _app_tile(self, item: dict) -> QFrame:
        tile = _AppTile(
            self, "apps", item.get("id") or "",
            lambda it=item: self._pick("apps", it),
            lambda it=item: self._open_app(it),
        )
        tile.setObjectName("BootAppTile")
        tile.setProperty("bootKind", "apps")
        tile.setProperty("bootId", item.get("id") or "")
        tile.setAttribute(Qt.WA_StyledBackground, True)
        frozen = not self._item_ready("apps", item)
        tile.setStyleSheet(self._item_qss("BootAppTile", frozen=frozen))
        tile.set_frozen(frozen)
        col = QVBoxLayout(tile)
        col.setContentsMargins(4, 4, 4, 4)
        col.setSpacing(2)
        icon = _pass_clicks(QLabel())
        icon.setFixedSize(_APP_ICON, _APP_ICON)
        icon.setAlignment(Qt.AlignCenter)
        target = item.get("target") or ""
        lnk = boot_desk.shortcut_path(item.get("lnk") or "")
        pix = self._pix(target, _APP_ICON)
        if pix.isNull():
            pix = self._pix(lnk, _APP_ICON)
        icon.setPixmap(pix)
        icon.setStyleSheet(self._label_qss(tk("text")))
        col.addWidget(icon, 0, Qt.AlignHCenter)
        name = _pass_clicks(_ElideLabel(item.get("name") or ""))
        name.set_full(item.get("name") or "")
        name.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        name_font = name.font()
        name_font.setPixelSize(_APP_NAME_PX)
        name.setFont(name_font)
        name.setStyleSheet(self._label_qss(tk("text")) + "font-size:%dpx;" % _APP_NAME_PX)
        col.addWidget(name)
        return tile

    # ── 添加 / 删除 ───────────────────────────────────────────

    def _confirm(self, title: str, text: str, extra: str = "") -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(title)
        box.setText(text)
        if extra:
            box.setInformativeText(extra)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        return box.exec_() == QMessageBox.Yes

    def _add_dir(self):
        path = QFileDialog.getExistingDirectory(self, "添加目录", "")
        if not path:
            return
        added, skipped = self._take_dirs([path])
        self._report_added("目录", added, skipped)

    def _add_site(self):
        raw, ok = QInputDialog.getText(self, "添加网站", "网址：")
        if not ok:
            return
        url = boot_desk.normalize_url(raw)
        if not url or url in ("https://", "http://"):
            message_box_warn(self, "网址空的", "先写网址。名称可以以后用「改名」。")
            return
        key = url.rstrip("/").lower()
        if any(boot_desk.normalize_url(x.get("url") or "").rstrip("/").lower() == key for x in self._sites):
            self._set_status("已经有了。", warn=True)
            return
        name = _site_name_from_url(url)
        item = {"id": _new_id(), "name": name, "url": url}
        self._sites.append(item)
        self._sel_undo = []
        self._sel = [("sites", item["id"])]
        self._rebuild_sites()
        self._mark()
        self._set_status("已添加网站：%s。要改显示名，点「改名」。" % name, ok=True)

    def _add_app(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "添加程序", "",
            "程序 (*.exe *.lnk);;所有文件 (*.*)",
        )
        if not path:
            return
        added, skipped, failed = self._take_apps([path])
        self._report_added("程序", added, skipped, failed)

    def _pick(self, kind: str, item: dict):
        iid = str(item.get("id") or "")
        if not iid or not self._item_ready(kind, item):
            return
        # 已经去点新卡，误清掉的那一组就不再恢复。
        if self._sel_undo:
            self._sel_undo = []
            self._clear_undo_status()
        key = (kind, iid)
        if key in self._sel:
            self._sel = [k for k in self._sel if k != key]
        else:
            self._sel.append(key)
        self._apply_pick_style()

    def _selected_items(self):
        pools = {"dirs": self._dirs, "sites": self._sites, "apps": self._apps}
        out = []
        kept = []
        for kind, iid in self._sel:
            item = next((x for x in pools.get(kind) or [] if x.get("id") == iid), None)
            if item is None:
                continue
            kept.append((kind, iid))
            out.append((kind, item))
        self._sel = kept
        return out

    def _sel_key_ready(self, key) -> bool:
        kind, iid = key
        pool = {"dirs": self._dirs, "sites": self._sites, "apps": self._apps}.get(kind) or []
        item = next((x for x in pool if str(x.get("id") or "") == str(iid)), None)
        return item is not None and self._item_ready(kind, item)

    def _apply_pick_style(self):
        self._sel = [key for key in self._sel if self._sel_key_ready(key)]
        self._sel_undo = [key for key in self._sel_undo if self._sel_key_ready(key)]
        chosen = set(self._sel)
        for name in ("BootDirCard", "BootSiteCard", "BootAppTile"):
            for card in self.findChildren(QFrame, name):
                key = (str(card.property("bootKind") or ""), str(card.property("bootId") or ""))
                frozen = bool(card.property("bootFrozen"))
                card.setStyleSheet(self._item_qss(name, key in chosen and not frozen, frozen))
        items = self._selected_items()
        if hasattr(self, "btn_rename"):
            one = len(items) == 1
            any_sel = len(items) > 0
            restore = bool(self._sel_undo) and not any_sel
            self.btn_open_sel.setText("恢复选中" if restore else "打开选中")
            self.btn_rename.setEnabled(one)
            self.btn_delete.setEnabled(any_sel)
            self.btn_open_sel.setEnabled(any_sel or restore)
            self.btn_rename.setCursor(Qt.PointingHandCursor if one else Qt.ArrowCursor)
            self.btn_delete.setCursor(Qt.PointingHandCursor if any_sel else Qt.ArrowCursor)
            self.btn_open_sel.setCursor(
                Qt.PointingHandCursor if (any_sel or restore) else Qt.ArrowCursor
            )

    def _clear_undo_status(self):
        if getattr(self.lbl_status, "_full", "") != "选中已取消，可点「恢复选中」。":
            return
        self.lbl_status.set_full(_HINT)
        self.lbl_status.setStyleSheet(self._label_qss(tk("text")))

    def _clear_selection(self):
        if not self._sel:
            return
        # 只记住误清掉的多选。只剩一张时，再点一次就能选回来。
        if len(self._sel) > 1:
            self._sel_undo = list(self._sel)
        else:
            self._sel_undo = []
            self._clear_undo_status()
        self._sel = []
        self._apply_pick_style()
        if self._sel_undo:
            self._set_status("选中已取消，可点「恢复选中」。", warn=True)

    def _restore_selection(self):
        self._sel = list(self._sel_undo)
        self._sel_undo = []
        self._apply_pick_style()
        n = len(self._selected_items())
        if n:
            self._set_status("已恢复选中 %s 项。" % n, ok=True)
        else:
            self._set_status("原先选中的内容已经不在了。", ok=False)

    def _on_open_sel_clicked(self):
        if self._sel_undo and not self._sel:
            self._restore_selection()
            return
        self._open_selected()

    def _press_hits_blank(self, obj) -> bool:
        widget = obj if isinstance(obj, QWidget) else None
        while widget is not None:
            if isinstance(widget, (_BootItem, QAbstractButton, QScrollBar)):
                return False
            if widget is self:
                return True
            widget = widget.parentWidget()
        return False

    def _watch_clicks(self, root):
        if root is None:
            return
        widgets = [root]
        try:
            widgets.extend(root.findChildren(QWidget))
        except Exception:
            pass
        for widget in widgets:
            if widget.property("_bootDropFilter"):
                continue
            widget.installEventFilter(self)
            widget.setProperty("_bootDropFilter", True)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._press_hits_blank(self):
            self._clear_selection()
        super().mousePressEvent(event)

    def _rename_selected(self):
        items = self._selected_items()
        if len(items) != 1:
            self._set_status("先只选一张卡，再改名。", ok=False)
            return
        kind, item = items[0]
        text, ok = QInputDialog.getText(
            self, "改名", "显示名称：", text=item.get("name") or "",
        )
        if not ok:
            return
        name = (text or "").strip()
        if not name:
            message_box_warn(self, "名称空的", "显示名称不能为空。")
            return
        item["name"] = name
        {"dirs": self._rebuild_dirs, "sites": self._rebuild_sites, "apps": self._rebuild_apps}[kind]()
        self._mark()
        self._set_status("已改名为：%s" % name, ok=True)

    def _delete_selected(self):
        items = self._selected_items()
        if not items:
            self._set_status("先点一张卡，再删除。", ok=False)
            return
        if len(items) == 1:
            kind, item = items[0]
            title = item.get("name") or ""
            if kind == "apps":
                if not self._confirm(
                    "删除程序",
                    "从开机员里删除「%s」？" % title,
                    "只删除复制到本程序 Boot 文件夹里的快捷方式，不删除原来的程序。",
                ):
                    return
            elif kind == "sites":
                if not self._confirm("删除网站", "从开机员里删除「%s」？" % title):
                    return
            else:
                if not self._confirm("删除目录", "从开机员里删除「%s」？" % title):
                    return
            self._drop_items(items)
            self._set_status("已删除：%s" % title, ok=True)
            return
        has_app = any(kind == "apps" for kind, _item in items)
        extra = "程序只删除开机员里的快捷方式，不删除原来的程序。" if has_app else ""
        if not self._confirm("删除选中", "删除选中的 %s 项？" % len(items), extra):
            return
        n = len(items)
        self._drop_items(items)
        self._set_status("已删除 %s 项。" % n, ok=True)

    def _drop_items(self, items):
        drop = {}
        for kind, item in items:
            drop.setdefault(kind, set()).add(item.get("id"))
            if kind != "apps":
                continue
            try:
                boot_desk.remove_shortcut(item.get("lnk") or "")
            except Exception:
                log.exception("删除快捷方式副本失败")
        self._sel_undo = []
        self._sel = [key for key in self._sel if key[1] not in drop.get(key[0], ())]
        if "dirs" in drop:
            self._dirs = [x for x in self._dirs if x.get("id") not in drop["dirs"]]
            self._rebuild_dirs()
        if "sites" in drop:
            self._sites = [x for x in self._sites if x.get("id") not in drop["sites"]]
            self._rebuild_sites()
        if "apps" in drop:
            self._apps = [x for x in self._apps if x.get("id") not in drop["apps"]]
            self._rebuild_apps()
        self._apply_pick_style()
        self._mark()

    def _arm_drop_tree(self, root, kind: str):
        self._arm_tree(root, external_kind=kind)

    def _arm_tree(self, root, external_kind=None):
        if root is None:
            return
        widgets = [root]
        try:
            widgets.extend(root.findChildren(QWidget))
        except Exception:
            pass
        for widget in widgets:
            widget.setAcceptDrops(True)
            if external_kind:
                widget.setProperty("bootDrop", external_kind)
            if not widget.property("_bootDropFilter"):
                widget.installEventFilter(self)
                widget.setProperty("_bootDropFilter", True)

    def eventFilter(self, obj, event):
        etype = event.type()
        if etype == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            if self._press_hits_blank(obj):
                self._clear_selection()
        if etype == QEvent.DragLeave:
            self._clear_reorder_lines_outside()
            return super().eventFilter(obj, event)
        if etype in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop):
            src_kind, src_id = _boot_mime(event.mimeData())
            if src_kind and src_id:
                return self._filter_reorder(obj, event, src_kind, src_id)
            kind = ""
            try:
                kind = str(obj.property("bootDrop") or "")
            except Exception:
                kind = ""
            if kind in ("dirs", "apps"):
                if etype in (QEvent.DragEnter, QEvent.DragMove):
                    if self._drop_acceptable(kind, event.mimeData()):
                        event.acceptProposedAction()
                    else:
                        event.ignore()
                    return True
                if etype == QEvent.Drop:
                    self._handle_drop(kind, event.mimeData())
                    event.acceptProposedAction()
                    return True
        return super().eventFilter(obj, event)

    def _section_kind(self, obj) -> str:
        widget = obj
        while widget is not None:
            try:
                kind = str(widget.property("bootSection") or "")
            except Exception:
                kind = ""
            if kind in ("dirs", "sites", "apps"):
                return kind
            if widget is self:
                break
            widget = widget.parentWidget()
        return ""

    def _host_for(self, kind: str):
        section = {"dirs": self.card_dirs, "sites": self.card_sites, "apps": self.card_apps}.get(kind)
        if not section:
            return None
        return section.get("host")

    def _pool_for(self, kind: str):
        return {"dirs": self._dirs, "sites": self._sites, "apps": self._apps}.get(kind) or []

    @staticmethod
    def _widget_in(obj, host) -> bool:
        widget = obj
        while widget is not None:
            if widget is host:
                return True
            widget = widget.parentWidget()
        return False

    def _host_cards(self, host):
        cards = []
        try:
            found = host.findChildren(_BootItem)
        except Exception:
            return cards
        for card in found:
            if card.parentWidget() is not host:
                continue
            geom = card.geometry()
            if not geom.isValid() or geom.isEmpty():
                continue
            cards.append(card)
        return cards

    def _drop_plan(self, host, kind: str, src_id: str, pos):
        """返回 (target_id, insert_after)。target_id 为空表示末尾。顺序不会变则返回 None。"""
        cards = self._host_cards(host)
        if not cards:
            return None
        flow = bool(host.property("bootFlow"))
        if flow:
            # 落在卡片上：左半插到前面，右半插到后面。落在缝里：按阅读顺序找插入点。
            hit = next((card for card in cards if card.geometry().contains(pos)), None)
            if hit is not None:
                target_id = hit._item_id
                insert_after = pos.x() >= hit.geometry().center().x()
            else:
                ordered = sorted(cards, key=lambda card: (card.geometry().top(), card.geometry().left()))
                target_id, insert_after = "", True
                for card in ordered:
                    geom = card.geometry()
                    before_row = pos.y() < geom.top()
                    on_row = geom.top() <= pos.y() <= geom.bottom() and pos.x() < geom.center().x()
                    if before_row or on_row:
                        target_id, insert_after = card._item_id, False
                        break
        else:
            cards.sort(key=lambda card: (card.geometry().top(), card.geometry().left()))
            target_id, insert_after = "", True
            for card in cards:
                if pos.y() < card.geometry().center().y():
                    target_id, insert_after = card._item_id, False
                    break
        _items, changed = _reorder_list(self._pool_for(kind), src_id, target_id, insert_after)
        if not changed:
            return None
        return target_id, insert_after

    def _gap_line(self, host, card, after: bool, flow: bool) -> QRect:
        geom = card.geometry()
        lay = host.layout()
        if flow:
            try:
                gap = int(lay.horizontalSpacing())
            except Exception:
                gap = 10
            x = geom.right() + gap // 2 if after else geom.left() - gap // 2 - 1
            x = max(0, min(x, max(0, host.width() - 2)))
            return QRect(x, geom.top(), 2, geom.height())
        try:
            gap = int(lay.spacing())
        except Exception:
            gap = 8
        y = geom.bottom() + gap // 2 if after else geom.top() - gap // 2 - 1
        y = max(0, min(y, max(0, host.height() - 2)))
        return QRect(geom.left(), y, geom.width(), 2)

    def _show_plan_line(self, host, target_id: str, insert_after: bool):
        cards = self._host_cards(host)
        if not cards:
            host.clear_drop_line()
            return
        flow = bool(host.property("bootFlow"))
        if not target_id:
            last = max(cards, key=lambda card: (card.geometry().bottom(), card.geometry().right()))
            host.set_drop_line(self._gap_line(host, last, True, flow))
            return
        card = next((item for item in cards if item._item_id == target_id), None)
        if card is None:
            host.clear_drop_line()
            return
        host.set_drop_line(self._gap_line(host, card, insert_after, flow))

    def _filter_reorder(self, obj, event, src_kind: str, src_id: str) -> bool:
        section = self._section_kind(obj)
        host = self._host_for(src_kind)
        if section != src_kind or host is None or not self._widget_in(obj, host):
            event.ignore()
            return True
        pos = host.mapFromGlobal(QCursor.pos())
        plan = self._drop_plan(host, src_kind, src_id, pos)
        if event.type() == QEvent.Drop:
            if plan is not None:
                target_id, insert_after = plan
                self._apply_reorder(src_kind, src_id, target_id, insert_after)
            host.clear_drop_line()
            event.acceptProposedAction()
            return True
        event.acceptProposedAction()
        if plan is None:
            host.clear_drop_line()
        else:
            target_id, insert_after = plan
            self._show_plan_line(host, target_id, insert_after)
        return True

    def _apply_reorder(self, kind: str, src_id: str, target_id: str, insert_after: bool):
        pool = self._pool_for(kind)
        items, changed = _reorder_list(pool, src_id, target_id, insert_after)
        if not changed:
            return
        if kind == "dirs":
            self._dirs = items
        elif kind == "sites":
            self._sites = items
        elif kind == "apps":
            self._apps = items
        else:
            return
        self._mark()
        self._schedule_rebuild(kind)

    def _schedule_rebuild(self, kind: str):
        self._reorder_rebuild_kind = kind
        if self._reorder_rebuild_queued:
            return
        self._reorder_rebuild_queued = True
        QTimer.singleShot(0, self._flush_reorder_rebuild)

    def _flush_reorder_rebuild(self):
        self._reorder_rebuild_queued = False
        kind = self._reorder_rebuild_kind
        self._reorder_rebuild_kind = ""
        rebuild = {
            "dirs": self._rebuild_dirs,
            "sites": self._rebuild_sites,
            "apps": self._rebuild_apps,
        }.get(kind)
        if rebuild is not None:
            rebuild()

    def _clear_reorder_lines(self):
        for section in (getattr(self, "card_dirs", None), getattr(self, "card_sites", None), getattr(self, "card_apps", None)):
            host = section.get("host") if isinstance(section, dict) else None
            if host is not None and hasattr(host, "clear_drop_line"):
                host.clear_drop_line()

    def _clear_reorder_lines_outside(self):
        pos = QCursor.pos()
        for section in (getattr(self, "card_dirs", None), getattr(self, "card_sites", None), getattr(self, "card_apps", None)):
            host = section.get("host") if isinstance(section, dict) else None
            if host is None or not hasattr(host, "clear_drop_line"):
                continue
            if not host.rect().contains(host.mapFromGlobal(pos)):
                host.clear_drop_line()

    @staticmethod
    def _mime_paths(mime) -> list:
        paths = []
        if mime is None or not mime.hasUrls():
            return paths
        for url in mime.urls():
            local = url.toLocalFile()
            if local:
                paths.append(local)
        return paths

    def _drop_acceptable(self, kind: str, mime) -> bool:
        if mime is None:
            return False
        if not mime.hasUrls() and not mime.hasFormat("text/uri-list"):
            return False
        paths = self._mime_paths(mime)
        if not paths:
            return True
        if kind == "dirs":
            return any(os.path.isdir(p) for p in paths)
        return any(_is_program_file(p) for p in paths)

    def _handle_drop(self, kind: str, mime):
        paths = self._mime_paths(mime)
        if kind == "dirs":
            folders = [p for p in paths if os.path.isdir(p)]
            if not folders:
                self._set_status("目录这里只能拖文件夹。", ok=False)
                return
            added, skipped = self._take_dirs(folders)
            self._report_added("目录", added, skipped)
            return
        programs = [p for p in paths if _is_program_file(p)]
        if not programs:
            self._set_status("程序这里只能拖 exe 或快捷方式。", ok=False)
            return
        added, skipped, failed = self._take_apps(programs)
        self._report_added("程序", added, skipped, failed)

    def _take_dirs(self, paths) -> tuple:
        added = 0
        skipped = 0
        last_id = ""
        seen = {os.path.normcase(os.path.abspath(x.get("path") or "")) for x in self._dirs}
        for raw in paths:
            path = os.path.abspath(raw)
            if not os.path.isdir(path):
                skipped += 1
                continue
            key = os.path.normcase(path)
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
            item = {
                "id": _new_id(),
                "name": os.path.basename(path) or path,
                "path": path,
            }
            self._dirs.append(item)
            last_id = item["id"]
            added += 1
        if added:
            self._sel_undo = []
            self._sel = [("dirs", last_id)]
            self._rebuild_dirs()
            self._mark()
        return added, skipped

    def _take_apps(self, paths) -> tuple:
        added = 0
        skipped = 0
        failed = []
        last_id = ""
        for raw in paths:
            if not _is_program_file(raw):
                skipped += 1
                continue
            name = os.path.splitext(os.path.basename(raw))[0]
            try:
                saved = boot_desk.install_program(raw, name)
            except boot_desk.BootDeskError as exc:
                failed.append(str(exc))
                continue
            except Exception as exc:
                log.exception("添加程序失败")
                failed.append(str(exc))
                continue
            lnk = saved.get("lnk") or ""
            target = (saved.get("target") or "").strip()
            duplicate = any((x.get("lnk") or "").lower() == lnk.lower() for x in self._apps)
            if not duplicate and target:
                duplicate = any(
                    os.path.normcase(x.get("target") or "") == os.path.normcase(target)
                    for x in self._apps
                )
            if duplicate:
                if saved.get("created"):
                    try:
                        boot_desk.remove_shortcut(lnk)
                    except Exception:
                        log.exception("收回重复快捷方式失败")
                skipped += 1
                continue
            saved.pop("created", None)
            saved["id"] = _new_id()
            saved["name"] = name
            self._apps.append(saved)
            last_id = saved["id"]
            added += 1
        if added:
            self._sel_undo = []
            self._sel = [("apps", last_id)]
            self._rebuild_apps()
            self._mark()
        return added, skipped, failed

    def _report_added(self, label: str, added: int, skipped: int, failed=None):
        failed = failed or []
        if added and not skipped and not failed:
            self._set_status("已添加 %s 个%s。" % (added, label), ok=True)
            return
        if not added and not failed:
            self._set_status("已经有了。", warn=True)
            return
        if failed and not added:
            message_box_warn(self, "没有加上", failed[0])
            return
        extra = (" " + failed[0]) if failed else ""
        dup = "，%s 个重复未添加" % skipped if skipped else ""
        self._set_status(
            "已添加 %s 个%s%s。%s" % (added, label, dup, extra),
            ok=not failed,
        )

    # ── 打开 ──────────────────────────────────────────────────

    def _set_status(self, text: str, ok: bool = True, warn: bool = False):
        if warn:
            color = tk("warn")
        else:
            color = tk("ok") if ok else tk("err")
        self.lbl_status.set_full(text)
        self.lbl_status.setStyleSheet(self._label_qss(color))

    def _open_dir(self, item: dict) -> bool:
        path = item.get("path") or ""
        if not os.path.isdir(path):
            self._set_status("找不到目录：%s" % path, ok=False)
            return False
        try:
            os.startfile(path)
        except Exception as exc:
            log.exception("打开目录失败")
            self._set_status("打开目录失败：%s" % exc, ok=False)
            return False
        return True

    def _open_site(self, item: dict) -> bool:
        url = boot_desk.normalize_url(item.get("url") or "")
        if not url:
            self._set_status("网址是空的。", ok=False)
            return False
        if not QDesktopServices.openUrl(QUrl(url)):
            self._set_status("打不开网站：%s" % url, ok=False)
            return False
        return True

    def _open_app(self, item: dict) -> bool:
        lnk = boot_desk.shortcut_path(item.get("lnk") or "")
        target = item.get("target") or ""
        path = lnk if lnk and os.path.isfile(lnk) else target
        if not path or not os.path.exists(path):
            self._set_status("找不到程序：%s" % (item.get("name") or ""), ok=False)
            return False
        try:
            os.startfile(path)
        except Exception as exc:
            log.exception("打开程序失败")
            self._set_status("打开程序失败：%s" % exc, ok=False)
            return False
        return True

    def _open_group(self, kind: str):
        if kind == "dirs":
            items = list(self._dirs)
            opener = self._open_dir
            empty = "还没有目录。"
        elif kind == "sites":
            items = list(self._sites)
            opener = self._open_site
            empty = "还没有网站。"
        else:
            items = list(self._apps)
            opener = self._open_app
            empty = "还没有程序。"
        if not items:
            self._set_status(empty, ok=False)
            return
        ready = [item for item in items if self._item_ready(kind, item)]
        missing = len(items) - len(ready)
        if not ready:
            label = {"dirs": "目录", "sites": "网站", "apps": "程序"}.get(kind, "内容")
            self._set_status("这台电脑上没有这些%s。" % label, ok=False)
            return
        failed = 0
        for item in ready:
            if not opener(item):
                failed += 1
        opened = len(ready) - failed
        if failed:
            self._set_status("打开了 %s 项，%s 项失败。" % (opened, failed), ok=False)
        elif missing:
            self._set_status("已打开 %s 项，%s 项这台电脑上没有。" % (opened, missing), warn=True)
        else:
            self._set_status("已打开 %s 项。" % opened, ok=True)

    def _open_selected(self):
        items = self._selected_items()
        if not items:
            self._set_status("先点选内容卡。", ok=False)
            return
        ready = [(kind, item) for kind, item in items if self._item_ready(kind, item)]
        missing = len(items) - len(ready)
        if not ready:
            self._set_status("这台电脑上没有选中的这些内容。", ok=False)
            return
        openers = {"dirs": self._open_dir, "sites": self._open_site, "apps": self._open_app}
        failed = []
        for kind, item in ready:
            if not openers[kind](item):
                failed.append(item.get("name") or kind)
        opened = len(ready) - len(failed)
        if failed:
            names = "、".join(failed[:6])
            self._set_status("打开了 %s 项，失败 %s 项：%s" % (opened, len(failed), names), ok=False)
        elif missing:
            self._set_status("已打开选中的 %s 项，%s 项这台电脑上没有。" % (opened, missing), warn=True)
        else:
            self._set_status("已打开选中的 %s 项。" % opened, ok=True)

    def _open_all(self):
        total = len(self._dirs) + len(self._sites) + len(self._apps)
        if total == 0:
            self._set_status("还没有记下目录、网站或程序。", ok=False)
            return
        failed = []
        missing = 0
        for item in self._dirs:
            if not self._item_ready("dirs", item):
                missing += 1
                continue
            path = item.get("path") or ""
            try:
                os.startfile(path)
            except Exception:
                log.exception("全部打开：目录失败")
                failed.append(item.get("name") or path)
        for item in self._sites:
            url = boot_desk.normalize_url(item.get("url") or "")
            if not url:
                failed.append(item.get("name") or "网站")
                continue
            QDesktopServices.openUrl(QUrl(url))
        for item in self._apps:
            if not self._item_ready("apps", item):
                missing += 1
                continue
            lnk = boot_desk.shortcut_path(item.get("lnk") or "")
            target = item.get("target") or ""
            path = lnk if lnk and os.path.isfile(lnk) else target
            if not path or not os.path.exists(path):
                failed.append(item.get("name") or "程序")
                continue
            try:
                os.startfile(path)
            except Exception:
                log.exception("全部打开：程序失败")
                failed.append(item.get("name") or path)
        opened = total - missing - len(failed)
        if opened <= 0 and not failed:
            self._set_status("这台电脑上没有可打开的目录或程序。", ok=False)
            return
        if failed:
            names = "、".join(failed[:6])
            self._set_status("打开了 %s 项，失败 %s 项：%s" % (opened, len(failed), names), ok=False)
        elif missing:
            self._set_status("已打开 %s 项，%s 项这台电脑上没有。" % (opened, missing), warn=True)
        else:
            self._set_status("已打开全部 %s 项。" % opened, ok=True)

    def _open_settings(self):
        try:
            boot_desk.open_startup_settings()
        except Exception as exc:
            log.exception("打开系统启动设置失败")
            message_box_warn(self, "没有打开", "打不开系统启动设置：%s" % exc)
