# pages/settings_section.py
# 「界面缩放 / Cookie设置 / 公共保存 / 预下载 / 数据管理 / 功能与说明」设置区。
# 原属「关于」页，现搬到「系统总览」页，抽成可复用组件。

from __future__ import annotations

import os
from typing import Dict, List

from PyQt5.QtCore import Qt, QTimer, QEvent, QRectF, pyqtSignal, QSize
from PyQt5.QtGui import QPixmap, QPainter, QPen, QBrush, QColor
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QFileDialog, QRadioButton, QButtonGroup, QSizePolicy, QAbstractButton,
    QDialog, QScrollArea, QFrame,
)

from styles.style_all import (
    make_card, install_card_title, restyle_card_title, theme, tk,
    format_ui_scale, UI_SCALE_OPTIONS,
    CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP,
    apply_mini_button,
)
from utils.app_paths import (
    user_downloads_dir, user_documents_dir,
    is_usable_public_save_dir, is_other_user_profile_path,
    apply_public_save_base, this_machine_downloads_dir,
)
from utils.flow_layout import FlowLayout
from utils.logger import get_logger

log = get_logger(__name__)

# (显示名, section, sub_key|None, cookie 域名匹配)
_COOKIE_ITEMS = (
    ("抖音", "video", "douyin", "douyin.com"),
    ("B站", "video", "bilibili", "bilibili.com"),
    ("YouTube", "video", "youtube", "youtube.com"),
    ("汽水音乐", "audio", "qishui", "qishui.douyin.com"),
    ("e-hentai.org", "gallery", None, "e-hentai.org"),
    ("pixiv", "gallery_pixiv", None, "pixiv.net"),
)

# 两行布局：分组标题 + 该行按钮显示名（与 _COOKIE_ITEMS 一致）
_COOKIE_ROWS = (
    ("视频下载", ("抖音", "B站", "YouTube")),
    ("音频下载", ("汽水音乐",)),
    ("图集下载", ("e-hentai.org", "pixiv")),
)

_CK_BTN_H = 22

# 功能说明图：扫描 guides；底部圆点数=真实图片数；≥2 张时 15 秒轮播
_GUIDE_SLOTS = 12
_GUIDE_CAROUSEL_MS = 15_000
# 功能与说明卡固定高度（px）：避免跟右侧五卡对高后图区过矮、上下被裁
_GUIDE_CARD_H = 590  # 展示图区：600-10
_GUIDE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")

# 圆点配色：与粘贴助手仓库七色同源，12 点循环取色
_GUIDE_DOT_COLORS = (
    "#ef4444",  # 红
    "#f97316",  # 橙
    "#eab308",  # 黄
    "#22c55e",  # 绿
    "#06b6d4",  # 青
    "#3b82f6",  # 蓝
    "#a855f7",  # 紫
)


class GuidePageDot(QAbstractButton):
    """功能说明翻页圆点。

    · 槽位固定不抖布局
    · 未选中直径 7px；选中直径 12px
    · 自绘抗锯齿圆 + 主题描边
    """

    D_ON = 12   # 选中直径（px）——大点不减
    D_OFF = 7   # 未选中直径（px）——小点减 1px
    # 槽位略大于选中圆，保证可点区域；相对旧 33px 收约 30% → 中心距更紧
    SLOT = max(D_ON + 4, int(round(33 * 0.70)))  # 23

    def __init__(self, color_hex: str, parent=None):
        super().__init__(parent)
        self._color = color_hex or "#3b82f6"
        self._dim = False  # 该槽暂无图片时略淡
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(self.SLOT, self.SLOT)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent; border: none;")

    def set_dim(self, dim: bool):
        dim = bool(dim)
        if self._dim != dim:
            self._dim = dim
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        checked = self.isChecked()
        d = float(self.D_ON if checked else self.D_OFF)
        x = (self.width() - d) / 2.0
        y = (self.height() - d) / 2.0

        fill = QColor(self._color)
        if self._dim and not checked:
            fill.setAlpha(100)
        p.setBrush(QBrush(fill))

        is_dark = True
        try:
            is_dark = bool(theme.is_dark)
        except Exception:
            pass

        if checked:
            ring = QColor("#ffffff") if is_dark else QColor("#0f172a")
            pen = QPen(ring, 2.0)
        elif self.underMouse():
            if is_dark:
                pen = QPen(QColor(255, 255, 255, 200), 1.4)
            else:
                pen = QPen(QColor(15, 23, 42, 140), 1.4)
        else:
            if is_dark:
                pen = QPen(QColor(255, 255, 255, 60), 1.0)
            else:
                pen = QPen(QColor(15, 23, 42, 50), 1.0)
        p.setPen(pen)
        p.drawEllipse(QRectF(x, y, d, d))
        p.end()

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)


def _app_root_dir() -> str:
    try:
        from utils.app_paths import app_root
        return app_root()
    except Exception:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _guides_dir() -> str:
    """用户放置功能图：平铺在 data/ 根，图片带 guide_ 前缀（如 guide_001.jpg）。"""
    try:
        from utils.app_paths import guides_user_dir
        return guides_user_dir()
    except Exception:
        d = _app_root_dir()
        try:
            os.makedirs(os.path.join(d, "data"), exist_ok=True)
        except Exception:
            pass
        return os.path.join(d, "data")


def _guides_scan_dirs() -> List[str]:
    """扫描顺序：用户 data/ 根优先，其次内置 assets/guides。"""
    user_d = _guides_dir()
    dirs: List[str] = [user_d]
    try:
        from utils.app_paths import guides_bundled_dir
        bundled = guides_bundled_dir()
        if os.path.normcase(os.path.normpath(bundled)) != os.path.normcase(
            os.path.normpath(user_d)
        ):
            dirs.append(bundled)
    except Exception:
        bundled = os.path.join(_app_root_dir(), "assets", "guides")
        if os.path.isdir(bundled):
            dirs.append(bundled)
    return dirs


def _list_guide_image_paths() -> List[str]:
    """扫描功能图，按文件名排序（最多 32 张）。

    · 用户图放 data/ 根、以 guide_ 开头（如 guide_0810.jpg）
    · 内置图在 assets/guides/（0801.jpg 之类）
    · 按“去掉 guide_ 前缀后的文件名”去重，用户目录优先，可覆盖内置同名图
    """
    by_key: Dict[str, str] = {}
    user_d = _guides_dir()
    for d in _guides_scan_dirs():
        if not os.path.isdir(d):
            continue
        is_user = os.path.normcase(os.path.normpath(d)) == os.path.normcase(
            os.path.normpath(user_d)
        )
        try:
            names = os.listdir(d)
        except Exception:
            continue
        for f in names:
            low = f.lower()
            if not any(low.endswith(ext) for ext in _GUIDE_EXTS):
                continue
            if is_user:
                if not f.startswith("guide_"):
                    continue  # data/ 根只认 guide_ 开头的用户图
                key = f[len("guide_"):].lower()
            else:
                key = low
            if key in by_key:
                continue  # 用户图已占同名
            by_key[key] = os.path.join(d, f)
    files = sorted(by_key.values(), key=lambda p: os.path.basename(p).lower())
    return files[:32]


class SettingsSection(QWidget):
    """左：功能与说明；右：界面缩放 + Cookie设置 + 公共保存 + 预下载 + 数据管理。

    系统总览页用 ``install_into(left_layout, right_layout)`` 把两栏
    嵌进整页两列，使「功能与说明」与「环境信息」同列、始终等宽。
    独立使用时仍可整块挂载本控件（左右 1:1，忽略 sizeHint 干扰）。
    """

    cookies_configured = pyqtSignal()

    # ===== 窗口高度 BUG 防护（与截图/速存/时区汇率页同一套路）=====
    # FlowLayout / wordWrap 会让整页 hasHeightForWidth()=True，Qt 拖窗口时抬高推荐高度。
    # 切断整页 HFW；内部滚动/换行照常。
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mw = None
        self._theme_titles: List[QLabel] = []
        self._cookie_paths: Dict[str, str] = {}
        self._cookie_btns: Dict[str, QPushButton] = {}
        self._cookie_row_labels: List[QLabel] = []
        self._cookie_auto_failed = False  # 自动查找 cookie 目录失败 → 手动按钮橙色
        self._save_path_autofilled = False
        self._loading = False
        self._guide_paths: List[str] = []
        self._guide_pixmaps: Dict[int, QPixmap] = {}
        self._guide_index = 0
        self._guide_pix_full = None  # type: QPixmap | None  # 当前显示的原图
        self._guide_dots: List[GuidePageDot] = []
        self._guide_dot_group = None  # type: QButtonGroup | None
        self._installed_split = False

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._persist_now)

        # 功能说明自动轮播（≥2 张图时启用）
        self._guide_carousel = QTimer(self)
        self._guide_carousel.setSingleShot(False)
        self._guide_carousel.setInterval(_GUIDE_CAROUSEL_MS)
        self._guide_carousel.timeout.connect(self._guide_carousel_tick)

        # 左右面板：可整块挂载，也可拆进系统总览的整页两列
        self.left_panel = QWidget()
        self.left_panel.setObjectName("SettingsLeftPanel")
        self.left_panel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.left_panel.setMinimumWidth(0)
        left_col = QVBoxLayout(self.left_panel)
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(8)
        left_col.setAlignment(Qt.AlignTop)
        # 顺序：界面缩放 → Cookie设置 → 公共保存 → 预下载开关 → 数据管理
        self._settings_cards = [
            self._build_scale_card(),
            self._build_cookie_card(),
            self._build_public_save_card(),
            self._build_pre_dl_card(),
            self._build_data_card(),
        ]
        for card in self._settings_cards:
            left_col.addWidget(card)

        self.right_panel = QWidget()
        self.right_panel.setObjectName("SettingsRightPanel")
        self.right_panel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.right_panel.setMinimumWidth(0)
        right_col = QVBoxLayout(self.right_panel)
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(8)
        right_col.setAlignment(Qt.AlignTop)
        right_col.addWidget(self._build_guide_card(), 0, Qt.AlignTop)
        self.right_panel.installEventFilter(self)
        self.left_panel.installEventFilter(self)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        root.addWidget(self.right_panel, 1)
        root.addWidget(self.left_panel, 1)

    def install_into(self, left_layout, right_layout):
        """把左右栏嵌进外部两列布局（与上方环境/资源监控同列对齐）。"""
        if self._installed_split:
            return
        # 从本控件 layout 中取出面板，交给外部列布局
        lay = self.layout()
        if lay is not None:
            lay.removeWidget(self.left_panel)
            lay.removeWidget(self.right_panel)
        left_layout.addWidget(self.right_panel, 0, Qt.AlignTop)
        right_layout.addWidget(self.left_panel, 0, Qt.AlignTop)
        self._installed_split = True
        # 壳控件不再占位（面板已重挂到外部列）
        self.hide()
        self.setFixedSize(0, 0)
        QTimer.singleShot(0, self._sync_guide_to_settings_height)

    # ── 对外 API ─────────────────────────────────────────────
    def set_main_window(self, mw):
        self._mw = mw

    def reload_from_prefs(self, prefs=None):
        """从主窗口内存偏好刷新控件。"""
        if prefs is None:
            prefs = getattr(self._mw, "_user_prefs", None) if self._mw is not None else None
        if not isinstance(prefs, dict):
            prefs = {}
        self._loading = True
        try:
            self._load_save_path(prefs)
            self._load_cookies(prefs)
            self._load_scale()
            self._load_pre_dl_auto(prefs)
        finally:
            self._loading = False
        # 启动/刷新后：空的公共保存目录补成系统「下载」；未齐的 Cookie 自动搜目录
        try:
            self._auto_fill_downloads_if_needed()
        except Exception:
            log.exception("自动识别下载目录失败")
        try:
            self._auto_scan_cookies_if_needed()
        except Exception:
            log.exception("自动查找 Cookie 目录失败")

    def set_cookie_alert(self, on: bool, text: str = None):
        """显示/隐藏「Cookie设置」卡内的缺失提示条。"""
        bar = getattr(self, "alert_bar", None)
        if bar is None:
            return
        if text:
            bar.setText(str(text))
        elif on:
            bar.setText("⚠ 检测到 Cookie 文件缺失，请重新配置")
        bar.setVisible(bool(on))
        self._apply_alert_style()
        QTimer.singleShot(0, self._sync_guide_to_settings_height)

    def _apply_alert_style(self):
        bar = getattr(self, "alert_bar", None)
        if bar is None:
            return
        try:
            warn = tk("warn")
        except Exception:
            warn = "#f59e0b"
        bar.setStyleSheet(
            f"QLabel#PathsCookieAlert{{"
            f"color:{warn};font-size:12px;font-weight:600;"
            f"background:rgba(245,158,11,0.12);border:1px solid {warn};"
            f"border-radius:8px;padding:8px 12px;}}"
        )

    def restyle_theme(self):
        """主题切换后重刷本组件内联样式。"""
        for lbl in self._theme_titles:
            try:
                restyle_card_title(lbl)
            except Exception:
                pass
        self._style_path_buttons()
        self._style_cookie_row_labels()
        self._apply_alert_style()
        self._style_guide_placeholder()
        self._style_guide_dots()
        QTimer.singleShot(0, self._sync_guide_to_settings_height)

    # ── 卡片：预下载自动处理（数据管理上方；仅区域名 + 开关）──
    def _build_pre_dl_card(self):
        # 懒导入：避免 settings_section ← page_overview ← ui_main 循环依赖
        from ui_main import SideToggleSwitch

        card = self._shrinkable_card(make_card("CardPreDlAuto"))
        box = QVBoxLayout(card)
        box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)

        # 「预下载」标题 + 开关 + 预处理文件（开关右侧）
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        title_lbl = install_card_title(card, title_row, "预下载", gap=0)
        self._theme_titles.append(title_lbl)
        title_row.addStretch(1)
        self.sw_pre_dl_auto = SideToggleSwitch(on_color="#06B6D4", parent=card)
        self.sw_pre_dl_auto.setChecked(False)
        self.sw_pre_dl_auto.setToolTip(
            "关：当前图集/视频下完后停止，不再开下一条\n"
            "开：空闲时按队列自动处理（打开后先等 3 秒）\n"
            "状态写入 user.txt；启动时若本机未加载 Cookie 会警告并关闭"
        )
        self.sw_pre_dl_auto.clicked.connect(self._on_pre_dl_auto_clicked)
        title_row.addWidget(self.sw_pre_dl_auto, 0, Qt.AlignVCenter)
        self.btn_pre_dl_file = apply_mini_button(QPushButton("预处理文件"))
        self.btn_pre_dl_file.setMinimumWidth(0)
        self.btn_pre_dl_file.setToolTip("查看并编辑预下载队列（records/pre_download.json）")
        self.btn_pre_dl_file.clicked.connect(self._on_pre_dl_file)
        title_row.addWidget(self.btn_pre_dl_file, 0, Qt.AlignVCenter)
        box.addLayout(title_row)
        return card

    def _load_pre_dl_auto(self, prefs: dict):
        # 优先跟主窗内存对齐；主窗尚未应用时读 user.txt
        mw = self._mw
        on = False
        if mw is not None and hasattr(mw, "is_pre_dl_auto_process_enabled"):
            on = bool(mw.is_pre_dl_auto_process_enabled())
        elif isinstance(prefs, dict):
            on = bool((prefs.get("clipboard_auto") or {}).get("auto_process"))
        sw = getattr(self, "sw_pre_dl_auto", None)
        if sw is None:
            return
        sw.setChecked(on)

    def _on_pre_dl_auto_clicked(self):
        if self._loading:
            return
        sw = getattr(self, "sw_pre_dl_auto", None)
        checked = bool(sw.isChecked()) if sw is not None else False
        mw = self._mw
        if mw is not None and hasattr(mw, "set_pre_dl_auto_process_enabled"):
            try:
                mw.set_pre_dl_auto_process_enabled(checked)
                return
            except Exception:
                log.exception("系统总览切换预下载自动处理失败")

    def sync_pre_dl_auto_checkbox(self, on: bool):
        """主窗改开关时同步本页滑动开关（不反写、不触发 clicked 副作用）。"""
        sw = getattr(self, "sw_pre_dl_auto", None)
        if sw is None:
            return
        on = bool(on)
        if bool(sw.isChecked()) == on:
            return
        sw.setChecked(on)

    def _on_pre_dl_file(self):
        """打开预处理文件弹窗：查看/编辑六平台 + 无法处理队列。"""
        mw = self._mw
        if mw is None:
            return
        try:
            from pages.pre_download_panel import open_pre_process_file_dialog

            def _after():
                try:
                    if hasattr(mw, "_refresh_pre_dl_panels"):
                        mw._refresh_pre_dl_panels()
                except Exception:
                    log.exception("预处理文件保存后刷新按钮失败")
                try:
                    if hasattr(mw, "_pre_dl_try_schedule"):
                        mw._pre_dl_try_schedule()
                except Exception:
                    log.exception("预处理文件保存后调度失败")

            open_pre_process_file_dialog(
                lambda: getattr(mw, "_pre_dl_store", None),
                mw,
                on_changed=_after,
            )
        except Exception:
            log.exception("打开预处理文件失败")

    def _shrinkable_card(self, card):
        """窄窗时卡片随列宽收缩，不把整页最小宽度顶爆。"""
        card.setMinimumWidth(0)
        card.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Maximum)
        return card

    # ── 卡片：数据管理（导出 / 导入 / 重置）──────────────────
    def _build_data_card(self):
        card = self._shrinkable_card(make_card("CardAboutInfo"))
        box = QVBoxLayout(card)
        box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        box.setSpacing(0)
        # 标题与三按钮同一行
        if hasattr(card, "setTitle"):
            try:
                card.setTitle("")
            except Exception:
                pass
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        lbl = QLabel("数据管理")
        lbl.setProperty("role", "card-title")
        restyle_card_title(lbl)
        lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._theme_titles.append(lbl)
        row.addWidget(lbl, 0, Qt.AlignVCenter)
        row.addStretch(1)
        for text, slot in (
            ("导出", self._on_export),
            ("导入", self._on_import),
            ("重置", self._on_reset),
        ):
            b = apply_mini_button(QPushButton(text))
            b.setMinimumWidth(0)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            row.addWidget(b, 0, Qt.AlignVCenter)
        box.addLayout(row)
        return card

    # ── 卡片：功能与说明（右侧）──────────────────────────────
    def _build_guide_card(self):
        card = self._shrinkable_card(make_card("CardFeatureGuide"))
        self.guide_card = card
        box = QVBoxLayout(card)
        # 展示图上下各留 6px；无标题，整卡留给说明图 + 圆点
        box.setContentsMargins(CARD_LEFT_GAP, 6, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        box.setSpacing(6)
        # 无卡片标题：整卡留给说明图 + 圆点

        self.lbl_guide_img = QLabel()
        self.lbl_guide_img.setObjectName("FeatureGuideImage")
        # 竖图等比缩放后高度常小于控件：垂直居中
        self.lbl_guide_img.setAlignment(Qt.AlignCenter)
        # 高度由右边 5 张设置卡决定，图区跟着卡片收；不要让原图像素 sizeHint 把卡撑高
        self.lbl_guide_img.setMinimumHeight(300)
        self.lbl_guide_img.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.lbl_guide_img.setScaledContents(False)
        box.addWidget(self.lbl_guide_img, 1)

        # 底部圆点：数量=真实图片数；固定一行水平排列
        self._guide_dot_host = QWidget()
        self._guide_dot_host.setObjectName("FeatureGuideDots")
        self._guide_dot_row = QHBoxLayout(self._guide_dot_host)
        self._guide_dot_row.setContentsMargins(0, 0, 0, 0)
        self._guide_dot_row.setSpacing(max(1, int(round(2 * 0.70))))
        self._guide_dot_row.addStretch(1)
        self._guide_dots = []
        self._guide_dot_group = QButtonGroup(self)
        self._guide_dot_group.setExclusive(True)
        box.addWidget(self._guide_dot_host)

        self._load_guide_images()
        self._rebuild_guide_dots()
        self._apply_guide_index(0)
        self._style_guide_placeholder()
        self._style_guide_dots()
        self._restart_guide_carousel()
        QTimer.singleShot(0, self._fit_guide_image)
        return card

    def _load_guide_images(self):
        """只记路径。原图大约一百多 MB，轮到哪张再读哪张。"""
        self._guide_paths = _list_guide_image_paths()
        self._guide_pixmaps = {}

    def _guide_count(self) -> int:
        return len(getattr(self, "_guide_paths", []) or [])

    def _guide_pixmap(self, idx: int) -> QPixmap:
        paths = getattr(self, "_guide_paths", []) or []
        cache = getattr(self, "_guide_pixmaps", None)
        if cache is None:
            cache = {}
            self._guide_pixmaps = cache
        if idx < 0 or idx >= len(paths):
            return QPixmap()
        pix = cache.get(idx)
        if pix is not None:
            return pix
        loaded = QPixmap(paths[idx])
        if loaded.isNull():
            loaded = QPixmap()
        cache[idx] = loaded
        return loaded

    def _rebuild_guide_dots(self):
        """按当前图片数重建底部轮播点（有几张图就几个点，单行居中）。"""
        row = getattr(self, "_guide_dot_row", None)
        group = getattr(self, "_guide_dot_group", None)
        if row is None:
            return
        while row.count():
            item = row.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                if group is not None:
                    try:
                        group.removeButton(w)
                    except Exception:
                        pass
                w.setParent(None)
                w.deleteLater()
        self._guide_dots = []
        if group is None:
            group = QButtonGroup(self)
            group.setExclusive(True)
            self._guide_dot_group = group

        row.addStretch(1)
        n = self._guide_count()
        for i in range(n):
            color = _GUIDE_DOT_COLORS[i % len(_GUIDE_DOT_COLORS)]
            d = GuidePageDot(color)
            d.setObjectName("FeatureGuideDot")
            group.addButton(d, i)
            d.clicked.connect(lambda checked=False, idx=i: self._set_guide_index(idx))
            self._guide_dots.append(d)
            row.addWidget(d)
        row.addStretch(1)
        self._style_guide_dots()

    def _guide_carousel_tick(self):
        """15 秒轮播：在有效图片间循环（跳过加载失败的空图）。"""
        n = self._guide_count()
        if n <= 1:
            self._guide_carousel.stop()
            return
        cur = int(getattr(self, "_guide_index", 0) or 0)
        nxt = cur
        for _ in range(n):
            nxt = (nxt + 1) % n
            pix = self._guide_pixmap(nxt)
            if pix is not None and not pix.isNull():
                break
        else:
            return
        self._apply_guide_index(nxt)

    def _restart_guide_carousel(self):
        """≥2 张图时启动/重置 15s 轮播；否则停止。"""
        t = getattr(self, "_guide_carousel", None)
        if t is None:
            return
        n = self._guide_count()
        if n <= 1:
            t.stop()
            return
        t.start()

    def _set_guide_index(self, idx: int):
        n = max(0, self._guide_count())
        if n <= 0:
            idx = 0
        else:
            idx = max(0, min(n - 1, int(idx)))
        if idx == self._guide_index and self._guide_pix_full is not None:
            # 仍刷新点样式，避免主题切换后状态不一致
            self._style_guide_dots()
            self._restart_guide_carousel()  # 手动点选也重计 15s
            return
        self._apply_guide_index(idx)
        self._restart_guide_carousel()

    def _apply_guide_index(self, idx: int):
        n = self._guide_count()
        if n <= 0:
            self._guide_index = 0
        else:
            self._guide_index = max(0, min(n - 1, int(idx)))
        lbl = getattr(self, "lbl_guide_img", None)
        if lbl is None:
            return

        if n == 0:
            self._guide_pix_full = None
            lbl.clear()
            guide_abs = _guides_dir()
            lbl.setText(
                "（未找到功能图）" + chr(10) + "请将图片放入：" + chr(10)
                + f"{guide_abs}" + chr(10)
                + "（文件名以 guide_ 开头，如 guide_001.jpg · "
                + "支持 jpg/png 等 · 有几张图底部就几个圆点 · ≥2 张 15 秒轮播）"
            )
        else:
            pix = self._guide_pixmap(self._guide_index)
            if pix is not None and not pix.isNull():
                self._guide_pix_full = pix
                lbl.setText("")
            else:
                self._guide_pix_full = None
                lbl.clear()
                lbl.setText(f"（第 {self._guide_index + 1} 张加载失败）")

        self._style_guide_placeholder()
        self._style_guide_dots()
        self._fit_guide_image()

    def _style_guide_dots(self):
        """同步圆点选中态（点数=真实图片数，无空槽）。"""
        for i, d in enumerate(getattr(self, "_guide_dots", []) or []):
            active = i == getattr(self, "_guide_index", 0)
            d.blockSignals(True)
            d.setChecked(active)
            d.blockSignals(False)
            if hasattr(d, "set_dim"):
                d.set_dim(False)
            d.update()

    def _style_guide_placeholder(self):
        lbl = getattr(self, "lbl_guide_img", None)
        if lbl is None:
            return
        try:
            mut = tk("text_mut")
        except Exception:
            mut = "#94a3b8"
        if self._guide_pix_full is None:
            lbl.setStyleSheet(
                f"QLabel#FeatureGuideImage{{color:{mut};font-size:12px;"
                f"background:transparent;border:none;}}"
            )
        else:
            lbl.setStyleSheet(
                "QLabel#FeatureGuideImage{background:transparent;border:none;}"
            )

    def _fit_guide_image(self):
        """功能图完整显示在卡内（等比缩放，高度方向居中，不裁切）。"""
        lbl = getattr(self, "lbl_guide_img", None)
        pix = getattr(self, "_guide_pix_full", None)
        if lbl is None:
            return
        if pix is None or pix.isNull():
            return
        w = max(40, lbl.width())
        h = max(40, lbl.height())
        scaled = pix.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        lbl.setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            self._fit_guide_image()
        except Exception:
            pass

    def eventFilter(self, obj, event):
        # 拆列后右栏 resize 不再经过本控件，靠 filter 缩放功能图
        if obj is self.right_panel and event.type() == QEvent.Resize:
            try:
                self._fit_guide_image()
            except Exception:
                pass
        if obj is self.left_panel and event.type() in (QEvent.Resize, QEvent.LayoutRequest):
            try:
                self._sync_guide_to_settings_height()
            except Exception:
                pass
        return super().eventFilter(obj, event)

    def _card_content_height(self, w, col_w: int) -> int:
        """单张设置卡在当前列宽下的内容高度（不用未约束 sizeHint）。"""
        if w is None:
            return 0
        if col_w > 1 and w.hasHeightForWidth():
            return max(0, int(w.heightForWidth(col_w)))
        if w.height() > 1:
            return max(0, int(w.height()))
        return max(0, int(w.sizeHint().height()))

    def _settings_stack_height(self) -> int:
        """右边 5 张设置卡 + 卡间距的总高度。

        必须按当前列宽实排。换行控件在宽度未定时 sizeHint 会偏高，
        旧逻辑再和实际高度取 max，会把「功能与说明」撑得比右边五卡高一截。
        """
        cards = [w for w in (getattr(self, "_settings_cards", None) or []) if w is not None and w.isVisible()]
        lp = getattr(self, "left_panel", None)
        lay = lp.layout() if lp is not None else None
        sp = int(lay.spacing()) if lay is not None else 8
        extra = 0
        if lay is not None:
            m = lay.contentsMargins()
            extra = int(m.top()) + int(m.bottom())
        if not cards:
            return extra
        col_w = int(lp.width()) if lp is not None and lp.width() > 1 else 0
        if lay is not None and col_w > 1 and lay.hasHeightForWidth():
            h = int(lay.heightForWidth(col_w))
            if h >= 80:
                return h
        total = extra
        for w in cards:
            total += self._card_content_height(w, col_w)
        if len(cards) > 1:
            total += sp * (len(cards) - 1)
        return total

    def _sync_guide_to_settings_height(self):
        """功能与说明用固定高度，保证图不被上下裁切。"""
        card = getattr(self, "guide_card", None)
        if card is None:
            return
        h = int(_GUIDE_CARD_H)
        if card.minimumHeight() == h and card.maximumHeight() == h:
            return
        card.setFixedHeight(h)
        card.updateGeometry()
        parent = card.parentWidget()
        if parent is not None:
            parent.updateGeometry()
        QTimer.singleShot(0, self._fit_guide_image)

    # ── 卡片：界面缩放 ───────────────────────────────────────
    def _build_scale_card(self):
        card = self._shrinkable_card(make_card("CardAboutScale"))
        box = QVBoxLayout(card)
        box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        self._theme_titles.append(install_card_title(card, box, "界面缩放"))

        hint = QLabel("整站缩放 · 切换倍数后自动重启应用")
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"QLabel{{color:{tk('text_mut')};font-size:12px;"
            f"background:transparent;border:none;}}"
        )
        box.addWidget(hint)

        scale_host = QWidget()
        scale_host.setMinimumWidth(0)
        scale_row = FlowLayout(scale_host, margin=0, h_spacing=12, v_spacing=4)
        self._scale_group = QButtonGroup(self)
        self._scale_group.setExclusive(True)
        self._scale_radios: Dict[float, QRadioButton] = {}
        for opt in UI_SCALE_OPTIONS:
            rb = QRadioButton(format_ui_scale(opt))
            rb.setStyleSheet("QRadioButton{background:transparent;}")
            rb.setCursor(Qt.PointingHandCursor)
            rb.setMinimumWidth(0)
            self._scale_group.addButton(rb)
            scale_row.addWidget(rb)
            self._scale_radios[float(opt)] = rb
            rb.toggled.connect(
                lambda checked, s=float(opt): self._on_scale_toggled(checked, s)
            )
        box.addWidget(scale_host)
        return card

    def _load_scale(self):
        cur = 1.0
        if self._mw is not None and hasattr(self._mw, "_read_active_qt_scale"):
            try:
                cur = float(self._mw._read_active_qt_scale())
            except Exception:
                cur = 1.0
        for opt, rb in self._scale_radios.items():
            rb.blockSignals(True)
            rb.setChecked(abs(float(opt) - cur) < 1e-6)
            rb.blockSignals(False)
        if not any(rb.isChecked() for rb in self._scale_radios.values()):
            nearest = min(UI_SCALE_OPTIONS, key=lambda x: abs(x - cur))
            self._scale_radios[float(nearest)].blockSignals(True)
            self._scale_radios[float(nearest)].setChecked(True)
            self._scale_radios[float(nearest)].blockSignals(False)

    def _on_scale_toggled(self, checked: bool, s: float):
        if not checked or self._loading or self._mw is None:
            return
        try:
            cur = float(self._mw._read_active_qt_scale())
        except Exception:
            cur = 1.0
        if abs(float(s) - cur) < 1e-6:
            return
        try:
            self._mw._apply_ui_scale(s, from_prefs=False, save=True, restart=True)
        except Exception:
            log.exception("切换界面缩放失败")

    # ── 卡片：Cookie设置 ──────────────────────────────────────
    def _build_cookie_card(self):
        card = self._shrinkable_card(make_card("CardAboutPaths"))
        box = QVBoxLayout(card)
        box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        self._theme_titles.append(install_card_title(card, box, "Cookie设置"))

        # Cookie 缺失提示条（启动检测发现配置路径无效时显示）
        self.alert_bar = QLabel("⚠ 检测到 Cookie 文件缺失，请重新配置")
        self.alert_bar.setObjectName("PathsCookieAlert")
        self.alert_bar.setWordWrap(True)
        self.alert_bar.setVisible(False)
        box.addWidget(self.alert_bar)
        self._apply_alert_style()

        hint = QLabel("Netscape .txt，可用浏览器扩展导出。")
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"QLabel{{color:{tk('text_mut')};font-size:12px;"
            f"background:transparent;border:none;}}"
        )
        box.addWidget(hint)

        self.btn_cookie_dir = QPushButton("手动指定Cookie目录")
        self.btn_cookie_dir.setCursor(Qt.PointingHandCursor)
        self.btn_cookie_dir.setMinimumWidth(0)
        self.btn_cookie_dir.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.btn_cookie_dir.clicked.connect(self._pick_cookie_dir_manual)
        box.addWidget(self.btn_cookie_dir, 0, Qt.AlignLeft)

        # 两行 Cookie：视频下载 → 抖音/B站/YouTube；图集下载 → e-hentai/pixiv
        # FlowLayout：窄列时自动换行，不把最小宽度顶出窗口
        name_to_item = {n: (n, sec, sub, dom) for n, sec, sub, dom in _COOKIE_ITEMS}
        self._cookie_row_labels = []
        for group_title, names in _COOKIE_ROWS:
            row_host = QWidget()
            row_host.setMinimumWidth(0)
            row = FlowLayout(row_host, margin=0, h_spacing=6, v_spacing=4)
            lab = QLabel(group_title)
            lab.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self._cookie_row_labels.append(lab)
            row.addWidget(lab)
            for name in names:
                if name not in name_to_item:
                    continue
                cb = QPushButton(name)
                cb.setCursor(Qt.PointingHandCursor)
                cb.setMinimumWidth(0)
                cb.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
                cb.clicked.connect(lambda checked=False, n=name: self._browse_cookie(n))
                self._cookie_btns[name] = cb
                row.addWidget(cb)
            box.addWidget(row_host)

        self._style_cookie_row_labels()
        self._style_path_buttons()
        return card

    # ── 卡片：公共保存 ────────────────────────────────────────
    def _build_public_save_card(self):
        card = self._shrinkable_card(make_card("CardPublicSave"))
        box = QVBoxLayout(card)
        box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        self._theme_titles.append(install_card_title(card, box, "公共保存"))

        hint = QLabel("派生速存 / 截图 / 录屏 / 图片处理 / 各下载子目录。")
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"QLabel{{color:{tk('text_mut')};font-size:12px;"
            f"background:transparent;border:none;}}"
        )
        box.addWidget(hint)

        save_row = QHBoxLayout()
        save_row.setSpacing(6)
        self.btn_browse = QPushButton("指定")
        self.btn_browse.setCursor(Qt.PointingHandCursor)
        self.btn_browse.setMinimumWidth(0)
        self.btn_browse.clicked.connect(self._browse_save_dir)
        save_row.addWidget(self.btn_browse)

        self.save_edit = QLineEdit()
        self.save_edit.setProperty("pathStyle", "folder")
        self.save_edit.setPlaceholderText("系统下载目录")
        self.save_edit.setMinimumWidth(0)
        self.save_edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.save_edit.textChanged.connect(self._schedule_persist)
        save_row.addWidget(self.save_edit, 1)

        self.btn_subdir = QPushButton("浏览")
        self.btn_subdir.setCursor(Qt.PointingHandCursor)
        self.btn_subdir.setMinimumWidth(0)
        self.btn_subdir.clicked.connect(self._open_subdir_dialog)
        save_row.addWidget(self.btn_subdir)
        box.addLayout(save_row)

        self._style_path_buttons()
        return card

    def _style_cookie_row_labels(self):
        try:
            mut = tk("text_mut")
        except Exception:
            mut = "#94a3b8"
        for lab in getattr(self, "_cookie_row_labels", []) or []:
            lab.setStyleSheet(
                f"QLabel{{color:{mut};font-size:12px;font-weight:600;"
                f"background:transparent;border:none;padding:0 2px 0 0;}}"
            )

    def _default_muted_btn_qss(self) -> str:
        try:
            mut = tk("text_mut")
            text = tk("text")
            hover = tk("accent_hover")
        except Exception:
            mut, text, hover = "#94a3b8", "#e2e8f0", "#60a5fa"
        return (
            f"QPushButton{{background:rgba(255,255,255,0.06);"
            f"border:1px solid rgba(255,255,255,0.22);border-radius:6px;"
            f"padding:2px 10px;font-weight:600;font-size:11px;"
            f"min-height:{_CK_BTN_H}px;max-height:{_CK_BTN_H}px;color:{mut};}}"
            f"QPushButton:hover{{border-color:{hover};color:{text};}}"
        )

    def _style_path_buttons(self):
        qss = self._default_muted_btn_qss()
        for attr in ("btn_browse", "btn_subdir"):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.setStyleSheet(qss)
        self._refresh_all_cookie_btns()
        self._refresh_cookie_action_btn()

    def _guess_base_path(self, prefs: dict) -> str:
        try:
            from utils.app_paths import guess_public_save_base
            return guess_public_save_base(prefs)
        except Exception:
            return (prefs.get("save_path_base") or "").strip()

    def _is_existing_dir(self, path: str) -> bool:
        p = (path or "").strip()
        if not p:
            return False
        try:
            return os.path.isdir(os.path.expanduser(p))
        except Exception:
            return False

    def _is_usable_save_dir(self, path: str) -> bool:
        """本机已有、且不是其他 Windows 用户主目录下的路径。"""
        try:
            return is_usable_public_save_dir(path)
        except Exception:
            return self._is_existing_dir(path)

    def _load_save_path(self, prefs: dict):
        stored = (prefs.get("save_path_base") or "").strip()
        detected = ""
        try:
            detected = (this_machine_downloads_dir() or "").strip()
        except Exception:
            detected = ""
        guessed = self._guess_base_path(prefs)

        self._save_path_autofilled = False
        # 旧电脑路径即便已被误建成目录，也不能继续用
        if self._is_usable_save_dir(stored):
            base = stored
        elif self._is_usable_save_dir(detected):
            base = detected
            self._save_path_autofilled = True
        elif self._is_usable_save_dir(guessed):
            base = guessed
            self._save_path_autofilled = True
        else:
            base = detected or os.path.expanduser("~/Downloads")
            self._save_path_autofilled = True

        self.save_edit.blockSignals(True)
        self.save_edit.setText((base or "").replace("\\", "/"))
        self.save_edit.blockSignals(False)

    def _auto_fill_downloads_if_needed(self):
        """空路径或无效/外机路径时，写入本机「下载」目录并落盘。"""
        cur = (self.save_edit.text() or "").strip()
        if self._is_usable_save_dir(cur) and not self._save_path_autofilled:
            return
        if self._is_usable_save_dir(cur):
            # 已显示本机有效目录（含刚识别出的下载目录）——仅在自动填入时落盘
            if self._save_path_autofilled:
                self._persist_now()
            return
        detected = ""
        try:
            detected = (this_machine_downloads_dir() or "").strip()
        except Exception:
            detected = ""
        if not detected:
            return
        shown = detected.replace("\\", "/")
        if (cur or "").replace("\\", "/") == shown:
            if self._save_path_autofilled:
                self._persist_now()
            return
        self.save_edit.blockSignals(True)
        self.save_edit.setText(shown)
        self.save_edit.blockSignals(False)
        self._save_path_autofilled = True
        self._persist_now()

    def _load_cookies(self, prefs: dict):
        for name, section, sub_key, _domain in _COOKIE_ITEMS:
            if sub_key:
                sec = prefs.get(section) or {}
                sub = sec.get(sub_key) if isinstance(sec.get(sub_key), dict) else {}
                ck = (sub or {}).get("cookie_path", "")
            else:
                sec = prefs.get(section) or {}
                ck = (sec or {}).get("cookie_path", "") if isinstance(sec, dict) else ""
            self._cookie_paths[name] = str(ck or "")
        self._refresh_all_cookie_btns()

    def _refresh_cookie_btn(self, name: str):
        btn = self._cookie_btns.get(name)
        if btn is None:
            return
        path = (self._cookie_paths.get(name) or "").strip()
        loaded = bool(path and os.path.isfile(path))
        btn.setText(name)
        if loaded:
            btn.setStyleSheet(
                f"QPushButton{{background:{tk('ok')};color:#ffffff;"
                f"border:1px solid {tk('ok')};border-radius:6px;"
                f"padding:2px 10px;font-weight:600;font-size:11px;"
                f"min-height:{_CK_BTN_H}px;max-height:{_CK_BTN_H}px;}}"
                f"QPushButton:hover{{background:{tk('ok')};}}"
            )
        else:
            try:
                mut = tk("text_mut")
                text = tk("text")
                hover = tk("accent_hover")
            except Exception:
                mut, text, hover = "#94a3b8", "#e2e8f0", "#60a5fa"
            btn.setStyleSheet(
                f"QPushButton{{background:rgba(255,255,255,0.06);"
                f"border:1px solid rgba(255,255,255,0.22);border-radius:6px;"
                f"padding:2px 10px;font-weight:600;font-size:11px;"
                f"min-height:{_CK_BTN_H}px;max-height:{_CK_BTN_H}px;color:{mut};}}"
                f"QPushButton:hover{{border-color:{hover};color:{text};}}"
            )

    def _refresh_all_cookie_btns(self):
        for name in self._cookie_btns:
            self._refresh_cookie_btn(name)
        self._refresh_cookie_action_btn()

    def _cookie_path_loaded(self, name: str) -> bool:
        path = (self._cookie_paths.get(name) or "").strip()
        return bool(path and os.path.isfile(path))

    def _cookies_all_loaded(self) -> bool:
        # 汽水 Cookie 可选，缺了不挡住「加载成功」
        names = [name for name, *_rest in _COOKIE_ITEMS if name != "汽水音乐"]
        if not names:
            return False
        return all(self._cookie_path_loaded(name) for name in names)

    def _refresh_cookie_action_btn(self):
        btn = getattr(self, "btn_cookie_dir", None)
        if btn is None:
            return
        try:
            mut = tk("text_mut")
            text = tk("text")
            hover = tk("accent_hover")
            warn = tk("warn")
        except Exception:
            mut, text, hover, warn = "#94a3b8", "#e2e8f0", "#60a5fa", "#f0a542"

        if self._cookies_all_loaded():
            btn.setText("Cookie加载成功")
            btn.setStyleSheet(
                f"QPushButton{{background:rgba(148,163,184,0.14);"
                f"border:1px solid rgba(148,163,184,0.28);border-radius:6px;"
                f"padding:2px 10px;font-weight:600;font-size:11px;"
                f"min-height:{_CK_BTN_H}px;max-height:{_CK_BTN_H}px;color:{mut};}}"
                f"QPushButton:hover{{border-color:rgba(148,163,184,0.45);color:{text};}}"
            )
            return

        btn.setText("手动指定Cookie目录")
        if self._cookie_auto_failed:
            btn.setStyleSheet(
                f"QPushButton{{background:rgba(240,165,66,0.18);"
                f"border:1px solid rgba(240,165,66,0.55);border-radius:6px;"
                f"padding:2px 10px;font-weight:700;font-size:11px;"
                f"min-height:{_CK_BTN_H}px;max-height:{_CK_BTN_H}px;color:{warn};}}"
                f"QPushButton:hover{{background:rgba(240,165,66,0.30);}}"
            )
        else:
            btn.setStyleSheet(
                f"QPushButton{{background:rgba(255,255,255,0.06);"
                f"border:1px solid rgba(255,255,255,0.22);border-radius:6px;"
                f"padding:2px 10px;font-weight:600;font-size:11px;"
                f"min-height:{_CK_BTN_H}px;max-height:{_CK_BTN_H}px;color:{mut};}}"
                f"QPushButton:hover{{border-color:{hover};color:{text};}}"
            )

    def _browse_save_dir(self):
        start = self.save_edit.text().strip() or user_downloads_dir() or os.path.expanduser("~")
        p = QFileDialog.getExistingDirectory(self, "选择公共保存目录", start)
        if p:
            self._save_path_autofilled = False
            self.save_edit.setText(p.replace("\\", "/"))

    def _open_subdir_dialog(self):
        """列出公共保存下每个子目录名，可单独改，保存后按新名字重派路径。"""
        from utils.app_paths import (
            PUBLIC_SAVE_SUBDIRS, clean_subdir_name, resolved_subdir_name,
            subdir_storage_key,
        )

        prefs = getattr(self._mw, "_user_prefs", None) if self._mw is not None else {}
        if not isinstance(prefs, dict):
            prefs = {}

        dlg = QDialog(self.window() or self)
        dlg.setWindowTitle("子目录")
        try:
            dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        except Exception:
            pass
        dlg.setMinimumWidth(420)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(14, 14, 14, 12)
        lay.setSpacing(8)

        hint = QLabel("这些文件夹都建在上面的公共保存目录下。只改名字。")
        hint.setWordWrap(True)
        try:
            mut = tk("text_mut")
        except Exception:
            mut = "#94a3b8"
        hint.setStyleSheet(
            f"QLabel{{color:{mut};font-size:12px;background:transparent;border:none;}}"
        )
        lay.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.verticalScrollBar().setStyleSheet(
            "QScrollBar:vertical{width:0px;margin:0;background:transparent;}"
        )
        host = QWidget()
        form = QVBoxLayout(host)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        edits = []
        for row in PUBLIC_SAVE_SUBDIRS:
            section, sub_key, default, label = row[0], row[1], row[2], row[3]
            line = QHBoxLayout()
            line.setSpacing(8)
            lab = QLabel(label)
            lab.setMinimumWidth(72)
            try:
                lab_c = tk("text")
            except Exception:
                lab_c = "#e2e8f0"
            lab.setStyleSheet(
                f"QLabel{{color:{lab_c};font-size:12px;"
                f"background:transparent;border:none;}}"
            )
            edit = QLineEdit(resolved_subdir_name(prefs, section, sub_key, default))
            edit.setPlaceholderText(default)
            line.addWidget(lab, 0)
            line.addWidget(edit, 1)
            form.addLayout(line)
            edits.append((section, sub_key, default, label, edit))
        scroll.setWidget(host)
        scroll.setMinimumHeight(min(420, 28 * len(edits) + 8))
        lay.addWidget(scroll, 1)

        err = QLabel("")
        err.setWordWrap(True)
        err.setVisible(False)
        try:
            err_c = tk("err")
        except Exception:
            err_c = "#ef4444"
        err.setStyleSheet(
            f"QLabel{{color:{err_c};font-size:12px;background:transparent;border:none;}}"
        )
        lay.addWidget(err)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        btns.addStretch(1)
        btn_save = QPushButton("保存并关闭")
        btn_close = QPushButton("关闭")
        btn_save.setCursor(Qt.PointingHandCursor)
        btn_close.setCursor(Qt.PointingHandCursor)
        qss = self._default_muted_btn_qss()
        btn_save.setStyleSheet(qss)
        btn_close.setStyleSheet(qss)
        btns.addWidget(btn_save)
        btns.addWidget(btn_close)
        lay.addLayout(btns)

        def _show_err(text):
            err.setText(text)
            err.setVisible(True)

        def _commit():
            names = {}
            seen = {}
            for section, sub_key, default, label, edit in edits:
                raw = edit.text()
                cleaned = clean_subdir_name(raw)
                if not cleaned:
                    _show_err(f"「{label}」的目录名不能为空，也不能包含 \\ / : * ? \" < > |")
                    edit.setFocus()
                    return
                fold = cleaned.casefold()
                if fold in seen:
                    _show_err(f"「{label}」和「{seen[fold]}」不能用同一个目录名")
                    edit.setFocus()
                    return
                seen[fold] = label
                names[subdir_storage_key(section, sub_key)] = cleaned
            base = (self.save_edit.text() or "").strip()
            if self._mw is None:
                _show_err("设置还没接上主窗口")
                return
            cur = getattr(self._mw, "_user_prefs", None)
            if not isinstance(cur, dict):
                _show_err("读不到当前配置")
                return
            old_names = cur.get("save_subdir_names") if isinstance(cur.get("save_subdir_names"), dict) else {}
            if base and os.path.isdir(base):
                moves = []
                leaving = set()
                planned = []
                for section, sub_key, default, label, _edit in edits:
                    key = subdir_storage_key(section, sub_key)
                    old = resolved_subdir_name(
                        {"save_subdir_names": old_names}, section, sub_key, default
                    )
                    new = names[key]
                    if old == new:
                        continue
                    old_dir = os.path.join(base, old)
                    new_dir = os.path.join(base, new)
                    planned.append((old_dir, new_dir, label, old))
                    if os.path.isdir(old_dir):
                        leaving.add(os.path.normcase(os.path.abspath(old_dir)))
                for old_dir, new_dir, label, _old in planned:
                    if not os.path.isdir(old_dir):
                        continue
                    if os.path.normcase(os.path.abspath(old_dir)) == os.path.normcase(os.path.abspath(new_dir)):
                        continue
                    if os.path.exists(new_dir) and os.path.normcase(os.path.abspath(new_dir)) not in leaving:
                        _show_err(f"「{label}」要改成的 {os.path.basename(new_dir)} 已经存在，没有改名")
                        return
                    moves.append((old_dir, new_dir))
                temps = []
                try:
                    for old_dir, new_dir in moves:
                        tmp = old_dir + ".__renaming"
                        n = 1
                        while os.path.exists(tmp):
                            tmp = old_dir + ".__renaming%d" % n
                            n += 1
                        os.rename(old_dir, tmp)
                        temps.append((tmp, new_dir, old_dir))
                    for tmp, new_dir, _old_dir in temps:
                        os.rename(tmp, new_dir)
                except Exception as exc:
                    for tmp, new_dir, old_dir in reversed(temps):
                        try:
                            if os.path.isdir(new_dir) and not os.path.exists(old_dir):
                                os.rename(new_dir, old_dir)
                            elif os.path.isdir(tmp) and not os.path.exists(old_dir):
                                os.rename(tmp, old_dir)
                        except Exception:
                            log.exception("回滚子目录改名失败")
                    log.exception("重命名子目录失败")
                    _show_err("改名没有完成：%s" % exc)
                    return
            cur["save_subdir_names"] = names
            self._persist_now()
            dlg.accept()

        btn_save.clicked.connect(_commit)
        btn_close.clicked.connect(dlg.reject)
        dlg.exec_()

    def _browse_cookie(self, name: str):
        start = (self._cookie_paths.get(name) or "").strip() or os.path.expanduser("~")
        p, _ = QFileDialog.getOpenFileName(
            self, f"选择 {name} Cookie", start,
            "Cookie 文件 (*.txt);;所有文件 (*.*)",
        )
        if p:
            self._cookie_paths[name] = p.replace("\\", "/")
            self._refresh_cookie_btn(name)
            self._refresh_cookie_action_btn()
            self._schedule_persist()
            self.cookies_configured.emit()

    def _list_cookie_txt_files(self, directory: str) -> list:
        files = []
        try:
            for f in os.listdir(directory):
                fp = os.path.join(directory, f)
                if (
                    os.path.isfile(fp)
                    and f.lower().endswith(".txt")
                    and os.path.getsize(fp) < 2 * 1024 * 1024
                ):
                    files.append(fp)
        except Exception:
            pass
        return files

    @staticmethod
    def _cookie_filename_score(fname: str, domain: str) -> int:
        low = (fname or "").lower()
        tokens = {
            "douyin.com": ("douyin",),
            "qishui.douyin.com": ("qishui", "汽水"),
            "bilibili.com": ("bilibili", "bili"),
            "youtube.com": ("youtube",),
            "e-hentai.org": ("e-hentai", "exhentai", "e_hentai"),
            "exhentai.org": ("exhentai", "e-hentai"),
            "pixiv.net": ("pixiv",),
        }
        hits = tokens.get(domain) or (domain.split(".")[0],)
        if not any(tok in low for tok in hits):
            return 0
        if low.endswith("_cookies.txt") or low.endswith("cookies.txt"):
            return 2
        return 1

    def _pick_cookie_file(self, txt_files, domain: str, extra_domain: str = None) -> str:
        scored = []
        for fp in txt_files:
            s = self._cookie_filename_score(os.path.basename(fp), domain)
            if extra_domain:
                s = max(s, self._cookie_filename_score(os.path.basename(fp), extra_domain))
            if s:
                scored.append((s, fp))
        if scored:
            scored.sort(key=lambda x: -x[0])
            return scored[0][1]
        needle = (domain or "").lower()
        extra = (extra_domain or "").lower()
        for fp in txt_files:
            try:
                with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                    low = fh.read(4096).lower()
                if needle and needle in low:
                    return fp
                if extra and extra in low:
                    return fp
            except Exception:
                continue
        return ""

    def _apply_cookie_dir(self, directory: str, *, overwrite: bool = False) -> int:
        """在目录顶层匹配 5 个 Cookie 文件。返回新写入的条数。"""
        if not directory or not os.path.isdir(directory):
            return 0
        txt_files = self._list_cookie_txt_files(directory)
        if not txt_files:
            return 0
        n = 0
        for name, _section, _sub_key, domain in _COOKIE_ITEMS:
            if domain == "e-hentai.org":
                extra = "exhentai.org"
            else:
                extra = None
            found = self._pick_cookie_file(txt_files, domain, extra)
            if not found:
                continue
            if not overwrite and self._cookie_path_loaded(name):
                continue
            self._cookie_paths[name] = found.replace("\\", "/")
            n += 1
        return n

    def _find_cookie_dirs(self) -> list:
        """在系统「下载」「文档」下找名为 cookie / cookies 的目录。"""
        dirs = []
        seen = set()

        def _add(p: str):
            if not p or not os.path.isdir(p):
                return
            try:
                key = os.path.normcase(os.path.abspath(p))
            except Exception:
                return
            if key in seen:
                return
            seen.add(key)
            dirs.append(p)

        roots = []
        try:
            dl = user_downloads_dir()
            if dl:
                roots.append(dl)
        except Exception:
            pass
        try:
            doc = user_documents_dir()
            if doc:
                roots.append(doc)
        except Exception:
            pass

        for root in roots:
            try:
                names = os.listdir(root)
            except Exception:
                continue
            for name in names:
                if name.lower() in ("cookie", "cookies"):
                    _add(os.path.join(root, name))
        return dirs

    def _auto_scan_cookies_if_needed(self):
        """5 个按钮未齐绿时，在下载/文档里找 cookie 目录并加载对应文件。"""
        self._cookie_auto_failed = False
        if self._cookies_all_loaded():
            self._refresh_cookie_action_btn()
            return

        cookie_dirs = self._find_cookie_dirs()
        if not cookie_dirs:
            self._cookie_auto_failed = True
            log.info("未在下载/文档目录找到 cookie 文件夹")
            self._refresh_cookie_action_btn()
            return

        changed = 0
        for d in cookie_dirs:
            changed += self._apply_cookie_dir(d, overwrite=False)
            if self._cookies_all_loaded():
                break

        self._refresh_all_cookie_btns()
        if changed:
            log.info("自动加载 Cookie：%d 个文件，目录=%s", changed, cookie_dirs)
            self._persist_now()
            self.cookies_configured.emit()
        else:
            self._refresh_cookie_action_btn()

    def _pick_cookie_dir_manual(self):
        start = ""
        for name, *_rest in _COOKIE_ITEMS:
            p = (self._cookie_paths.get(name) or "").strip()
            if p:
                d = os.path.dirname(p)
                if os.path.isdir(d):
                    start = d
                    break
        if not start:
            try:
                start = user_documents_dir() or user_downloads_dir() or ""
            except Exception:
                start = ""
        if not start:
            start = os.path.expanduser("~")
        p = QFileDialog.getExistingDirectory(self, "选择 Cookie 目录", start)
        if not p:
            return
        self._cookie_auto_failed = False
        self._apply_cookie_dir(p, overwrite=True)
        self._refresh_all_cookie_btns()
        self._persist_now()
        self.cookies_configured.emit()

    # ── 数据管理 ─────────────────────────────────────────────
    def _on_export(self):
        if self._mw and hasattr(self._mw, "_on_export_records"):
            self._mw._on_export_records(self)

    def _on_import(self):
        if self._mw and hasattr(self._mw, "_on_import_records"):
            self._mw._on_import_records(self)

    def _on_reset(self):
        if self._mw and hasattr(self._mw, "_on_reset_records"):
            self._mw._on_reset_records(self)

    # ── 持久化 ───────────────────────────────────────────────
    def _schedule_persist(self, *_args):
        if self._loading:
            return
        self._save_timer.start()

    def _persist_now(self):
        if self._mw is None:
            return
        try:
            from utils.user_prefs import save_user_prefs, default_prefs
            import copy

            cur = getattr(self._mw, "_user_prefs", None)
            if not isinstance(cur, dict):
                cur = default_prefs()
                self._mw._user_prefs = cur
            # 就地更新，避免 _load_user_prefs 里仍握着旧 dict 的引用
            new_prefs = copy.deepcopy(cur)

            base = self.save_edit.text().strip()
            # 换电脑留下的 Users\\旧用户 路径：即使框里还有，也改回本机下载目录，绝不 mkdir 去造
            if not base or is_other_user_profile_path(base):
                base = this_machine_downloads_dir() or os.path.expanduser("~/Downloads")
                shown = base.replace("\\", "/")
                if (self.save_edit.text() or "").strip().replace("\\", "/") != shown:
                    self.save_edit.blockSignals(True)
                    self.save_edit.setText(shown)
                    self.save_edit.blockSignals(False)
            apply_public_save_base(new_prefs, base, mkdir=True)

            for name, section, sub_key, _domain in _COOKIE_ITEMS:
                ck = (self._cookie_paths.get(name) or "").strip()
                if sub_key:
                    sec = new_prefs.get(section)
                    if not isinstance(sec, dict):
                        sec = {}
                        new_prefs[section] = sec
                    s = sec.get(sub_key)
                    if not isinstance(s, dict):
                        s = {}
                        sec[sub_key] = s
                    s["cookie_path"] = ck
                else:
                    sec = new_prefs.get(section)
                    if not isinstance(sec, dict):
                        sec = {}
                        new_prefs[section] = sec
                    sec["cookie_path"] = ck

            cur.clear()
            cur.update(new_prefs)
            self._mw._user_prefs = cur
            save_user_prefs(cur)

            # 同步到各业务页
            try:
                self._mw.page_douyin.apply_settings(new_prefs.get("video") or {})
            except Exception:
                pass
            try:
                pa = getattr(self._mw, "page_audio", None)
                if pa is not None and hasattr(pa, "apply_settings"):
                    pa.apply_settings(new_prefs.get("audio") or {})
            except Exception:
                pass
            try:
                self._mw.page_gallery.apply_settings(new_prefs.get("gallery") or {})
                pixiv = getattr(self._mw.page_gallery, "page_pixiv", None)
                if pixiv and hasattr(pixiv, "apply_settings"):
                    pixiv.apply_settings(new_prefs.get("gallery_pixiv") or {})
                hitomi = getattr(self._mw.page_gallery, "page_hitomi", None)
                gal = new_prefs.get("gallery") or {}
                if hitomi and hasattr(hitomi, "apply_settings") and isinstance(gal, dict):
                    hitomi.apply_settings(gal.get("hitomi") or {})
            except Exception:
                pass
            try:
                self._mw.page_fast.apply_settings(new_prefs.get("fast_save") or {})
            except Exception:
                pass
            try:
                self._mw.page_shot.apply_settings(new_prefs.get("screenshot") or {})
            except Exception:
                pass
            try:
                self._mw.page_region_rec.apply_settings(new_prefs.get("region_record") or {})
            except Exception:
                pass
            try:
                page_ip = getattr(self._mw, "page_img_proc", None)
                if page_ip is not None and hasattr(page_ip, "apply_settings"):
                    page_ip.apply_settings(new_prefs.get("image_proc") or {})
            except Exception:
                pass
            try:
                # 语音克隆 = 「音频处理」页的 CosVoice3 页签
                page_a = getattr(self._mw, "page_auk", None)
                page_vc = getattr(page_a, "page_cosvoice3", None) if page_a is not None else None
                if page_vc is not None and hasattr(page_vc, "apply_settings"):
                    page_vc.apply_settings(new_prefs.get("voice_clone") or {})
            except Exception:
                pass
            log.info("系统总览设置已落盘")
        except Exception:
            log.exception("系统总览保存设置失败")
