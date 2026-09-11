from styles.style_all import (
    TEXT_STYLE,
    BUTTON_STYLE,
    LINEEDIT_STYLE,
    install_card_title,
    restyle_card_title,
    make_card,
    apply_folder_path_edit,
    restyle_folder_path_edit,
    apply_medium_button,
    apply_transparent_surface,
    theme,
    tk,
    CARD_BOTTOM_GAP,
    CARD_TITLE_BODY_GAP,
)

# TEXT_STYLE 本身是空字符串（见 style_common.py），setStyleSheet(TEXT_STYLE) 等价于
# setStyleSheet("")，文字颜色/透明背景全靠祖先 QGroupBox[titleVariant="accent"] 的级联
# 规则兜底。为避免"心存侥幸"（见标准 3.3/3.9），这里显式追加 background: transparent，
# 页面内所有原来 setStyleSheet(TEXT_STYLE) 的地方统一换成 TEXT_STYLE_T。
TEXT_STYLE_T = TEXT_STYLE + "background: transparent;"

import os
import subprocess
import ctypes
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QListWidget, QCheckBox, QFileDialog,
    QSizePolicy, QFrame,
)
from PyQt5.QtCore import Qt

from pages.disk_treemap_widget import DiskAnalyzerWidget

# 分隔线粗细（px）——与「比例计算」一致
_SEP_PX = 3


def _make_h_sep() -> QFrame:
    f = QFrame()
    f.setObjectName("DirHSep")
    f.setFrameShape(QFrame.NoFrame)
    f.setFixedHeight(_SEP_PX)
    f.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    f.setAttribute(Qt.WA_StyledBackground, True)
    return f


def _make_v_sep() -> QFrame:
    f = QFrame()
    f.setObjectName("DirVSep")
    f.setFrameShape(QFrame.NoFrame)
    f.setFixedWidth(_SEP_PX)
    f.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
    f.setAttribute(Qt.WA_StyledBackground, True)
    return f


def _is_admin():
    """检测当前进程是否具有管理员权限"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def _run_mklink(link_path: str, target_path: str) -> tuple[bool, str]:
    """
    执行 mklink /D 命令。
    用 list 形式传参避免路径中文/空格被 shell 解析出错。
    返回 (成功与否, 输出信息)。
    """
    try:
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/D", link_path, target_path],
            capture_output=True,
            encoding="gbk",
            errors="replace"
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if result.returncode == 0:
            return True, stdout or "命令执行成功"
        else:
            return False, stderr or stdout or f"返回码：{result.returncode}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


class PageDirLink(QWidget):
    def __init__(self):
        super().__init__()
        apply_transparent_surface(self, "PageDirLink")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)  # 上下区间「空隙+横线+空隙」显式控制

        self._theme_titles = []
        self._h_seps = []
        self._v_seps = []

        # ===================================================================
        # 上方区域：左「新建映射」｜竖线｜右「映射记录」
        # Tab pane 已是外框，内部分区 borderless，不再套矩形卡
        # ===================================================================
        self.link_section = QWidget()
        apply_transparent_surface(self.link_section, "DirLinkSection")
        link_h = QHBoxLayout(self.link_section)
        link_h.setContentsMargins(0, 0, 0, 0)
        link_h.setSpacing(0)

        # ── 左侧：新建映射（无外框） ──
        gb = make_card("CardDirLinkCreate", borderless=True)
        vb = QVBoxLayout(gb)
        vb.setSpacing(CARD_TITLE_BODY_GAP)
        vb.setContentsMargins(0, 0, 0, CARD_BOTTOM_GAP)
        self._theme_titles.append(install_card_title(gb, vb, "新建映射"))

        body_create = QWidget()
        # 必须带 #id 选择器：无选择器的 border:none 会级联到子 QLineEdit，冲掉路径框外框
        body_create.setObjectName("DirLinkCreateBody")
        body_create.setStyleSheet(
            "#DirLinkCreateBody{background:transparent;border:none;}"
        )
        vb_body = QVBoxLayout(body_create)
        vb_body.setContentsMargins(0, 0, 0, 0)
        vb_body.setSpacing(6)
        vb.addWidget(body_create, 1)

        # 行1：源目录（mklink 的 target，即真实目录）
        r1 = QHBoxLayout()
        lbl_src = QLabel("源目录（真实路径）：")
        lbl_src.setStyleSheet(TEXT_STYLE_T)
        self.src_path = QLineEdit()
        self._src_path_icon_action = apply_folder_path_edit(self.src_path)
        self.src_path.setPlaceholderText("D:\\实际存放内容的文件夹")
        self.src_path.textChanged.connect(self._update_preview)
        btn_src = apply_medium_button(QPushButton("浏览"))
        btn_src.clicked.connect(self._choose_src)
        r1.addWidget(lbl_src)
        r1.addWidget(self.src_path)
        r1.addWidget(btn_src)
        vb_body.addLayout(r1)

        # 行2：链接位置（mklink 的 link，即软链接路径）
        r2 = QHBoxLayout()
        lbl_dst = QLabel("链接位置（新路径）：")
        lbl_dst.setStyleSheet(TEXT_STYLE_T)
        self.dst_path = QLineEdit()
        self._dst_path_icon_action = apply_folder_path_edit(self.dst_path)
        self.dst_path.setPlaceholderText("C:\\Users\\...\\想要访问的路径名称")
        self.dst_path.textChanged.connect(self._update_preview)
        btn_dst = apply_medium_button(QPushButton("浏览"))
        btn_dst.clicked.connect(self._choose_dst)
        r2.addWidget(lbl_dst)
        r2.addWidget(self.dst_path)
        r2.addWidget(btn_dst)
        vb_body.addLayout(r2)

        # 行3：命令预览（只读）
        r3 = QHBoxLayout()
        lbl_cmd = QLabel("将执行：")
        lbl_cmd.setStyleSheet(TEXT_STYLE_T)
        self.cmd_preview = QLineEdit()
        self.cmd_preview.setStyleSheet(LINEEDIT_STYLE)
        self.cmd_preview.setReadOnly(True)
        self.cmd_preview.setPlaceholderText("填写路径后自动显示命令")
        r3.addWidget(lbl_cmd)
        r3.addWidget(self.cmd_preview, 1)
        vb_body.addLayout(r3)

        # 行4：选项 + 执行按钮
        r4 = QHBoxLayout()
        self.cb_admin = QCheckBox("以管理员权限执行（推荐）")
        self.cb_admin.setStyleSheet(TEXT_STYLE_T)
        self.cb_admin.setChecked(True)
        r4.addWidget(self.cb_admin)
        r4.addStretch()
        self.btn_run = QPushButton("▶ 创建映射")
        self.btn_run.setStyleSheet(BUTTON_STYLE)
        self.btn_run.clicked.connect(self._create_link)
        r4.addWidget(self.btn_run)
        vb_body.addLayout(r4)

        # 左表单 : 右记录 ≈ 5:3
        link_h.addWidget(gb, 5)

        # 列间：空隙 + 竖线 + 空隙
        link_h.addSpacing(12)
        v_mid = _make_v_sep()
        self._v_seps.append(v_mid)
        link_h.addWidget(v_mid)
        link_h.addSpacing(12)

        # ── 右侧：映射记录（无外框） ──
        log_gb = make_card("CardDirLinkLog", borderless=True)
        log_gb.setMinimumWidth(200)
        log_gb.setMaximumWidth(320)
        log_lay = QVBoxLayout(log_gb)
        log_lay.setContentsMargins(0, 0, 0, CARD_BOTTOM_GAP)
        log_lay.setSpacing(CARD_TITLE_BODY_GAP)
        self._theme_titles.append(install_card_title(log_gb, log_lay, "映射记录"))

        self.list = QListWidget()
        self.list.setStyleSheet(f"QListWidget{{color:{tk('text_mut')};}}")
        self.list.setAttribute(Qt.WA_StyledBackground, True)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        log_lay.addWidget(self.list)

        link_h.addWidget(log_gb, 3)

        # 初始日志
        admin_hint = (
            "✅ 当前已是管理员权限"
            if _is_admin()
            else "⚠️ 当前为普通权限（推荐）。系统盘映射请勾选「以管理员权限执行」"
        )
        self.list.addItem("📁 目录映射页面已就绪")
        self.list.addItem(admin_hint)

        # ===================================================================
        # 下方区域：磁盘分析（无外框）
        # ===================================================================
        disk_gb = make_card("CardDirLinkDisk", borderless=True)
        disk_gb_lay = QVBoxLayout(disk_gb)
        disk_gb_lay.setContentsMargins(0, 0, 0, 0)
        disk_gb_lay.setSpacing(CARD_TITLE_BODY_GAP)
        self._theme_titles.append(install_card_title(disk_gb, disk_gb_lay, "磁盘分析"))

        self.disk_analyzer = DiskAnalyzerWidget()
        disk_gb_lay.addWidget(self.disk_analyzer)

        # 上下：映射区约 22% + 空隙/横线 + 磁盘分析约 78%
        outer.addWidget(self.link_section, 22)
        outer.addSpacing(10)
        h_mid = _make_h_sep()
        self._h_seps.append(h_mid)
        outer.addWidget(h_mid)
        outer.addSpacing(10)
        outer.addWidget(disk_gb, 78)

        self._restyle_seps()
        theme.changed.connect(self.refresh_theme)

    def shutdown(self):
        """主窗口关窗：停磁盘扫描线程。"""
        da = getattr(self, "disk_analyzer", None)
        if da is not None and hasattr(da, "shutdown"):
            try:
                da.shutdown()
            except Exception:
                pass

    # ── 路径浏览 ────────────────────────────────────────────────────


    def _restyle_seps(self):
        """横/竖 3px 实线，随主题 border 色。"""
        color = tk("border")
        h_qss = (
            "QFrame#DirHSep{"
            f"background:{color};border:none;"
            f"max-height:{_SEP_PX}px;min-height:{_SEP_PX}px;}}"
        )
        v_qss = (
            "QFrame#DirVSep{"
            f"background:{color};border:none;"
            f"max-width:{_SEP_PX}px;min-width:{_SEP_PX}px;}}"
        )
        for f in self._h_seps:
            f.setStyleSheet(h_qss)
        for f in self._v_seps:
            f.setStyleSheet(v_qss)

    def refresh_theme(self, *_):
        for lbl in self._theme_titles:
            restyle_card_title(lbl)
        self._restyle_seps()
        self.list.setStyleSheet(f"QListWidget{{color:{tk('text_mut')};}}")
        if hasattr(self, "src_path"):
            restyle_folder_path_edit(self.src_path, getattr(self, "_src_path_icon_action", None))
        if hasattr(self, "dst_path"):
            restyle_folder_path_edit(self.dst_path, getattr(self, "_dst_path_icon_action", None))

    def _choose_src(self):
        d = QFileDialog.getExistingDirectory(self, "选择源目录（真实文件夹）")
        if d:
            self.src_path.setText(os.path.normpath(d))

    def _choose_dst(self):
        """
        链接位置允许填写不存在的路径（mklink 会新建），
        所以这里让用户选择父目录后手动补全名称，或者直接手动输入。
        """
        d = QFileDialog.getExistingDirectory(self, "选择链接位置的父目录")
        if d:
            # 用源目录的最后一级名作为默认链接名
            src = self.src_path.text().strip()
            default_name = os.path.basename(src) if src else "新链接"
            self.dst_path.setText(os.path.join(os.path.normpath(d), default_name))

    # ── 命令预览 ────────────────────────────────────────────────────

    def _update_preview(self):
        src = self.src_path.text().strip()
        dst = self.dst_path.text().strip()
        if src and dst:
            self.cmd_preview.setText(f'mklink /D "{dst}" "{src}"')
        else:
            self.cmd_preview.clear()

    # ── 创建映射 ────────────────────────────────────────────────────

    def _create_link(self):
        src = self.src_path.text().strip()
        dst = self.dst_path.text().strip()

        # 基本校验
        if not src or not dst:
            self.list.addItem("⚠️ 源目录和链接位置均不能为空")
            return
        if not os.path.isdir(src):
            self.list.addItem(f"❌ 源目录不存在：{src}")
            return
        if os.path.islink(dst):
            self.list.addItem(f"❌ 该路径已是软链接，请先删除再重建：{dst}")
            return
        if os.path.exists(dst):
            self.list.addItem("❌ 链接位置已存在真实文件夹，mklink 无法覆盖")
            self.list.addItem(f"   请先删除或改名后再映射：{dst}")
            return

        # 确保链接位置的父目录存在（mklink 不会自动建中间目录）
        parent = os.path.dirname(dst)
        if parent and not os.path.isdir(parent):
            try:
                os.makedirs(parent, exist_ok=True)
                self.list.addItem(f"📂 已自动创建父目录：{parent}")
            except Exception as e:
                self.list.addItem(f"❌ 无法创建父目录 {parent}：{e}")
                return
        elif parent:
            self.list.addItem(f"📂 父目录已存在：{parent}")

        cmd_str = f'mklink /D "{dst}" "{src}"'
        self.list.addItem(f"▶ 执行：{cmd_str}")

        # 需要提权时，用 ShellExecute 重启一个 cmd 窗口
        if self.cb_admin.isChecked() and not _is_admin():
            try:
                ret = ctypes.windll.shell32.ShellExecuteW(
                    None, "runas", "cmd.exe",
                    f'/c {cmd_str} && pause',
                    None, 1
                )
                if ret > 32:
                    self.list.addItem("🔐 已请求管理员权限，请在弹出的 CMD 窗口中确认")
                else:
                    self.list.addItem(f"❌ 提权失败（ShellExecute 返回 {ret}），请手动以管理员运行")
            except Exception as e:
                self.list.addItem(f"❌ 提权异常：{type(e).__name__}: {e}")
            return

        # 当前已是管理员，或用户不勾选提权，直接执行
        ok, msg = _run_mklink(dst, src)
        if ok:
            self.list.addItem(f"✅ 创建成功：{msg}")
            self.list.addItem(f"   链接：{dst}  →  {src}")
        else:
            self.list.addItem(f"❌ 创建失败：{msg}")
            if "语法不正确" in msg or "syntax" in msg.lower():
                self.list.addItem("   可能原因：父目录不存在，或链接位置路径有误")
            elif "拒绝访问" in msg or "Access" in msg:
                self.list.addItem("   可能原因：权限不足，请以管理员身份运行程序")

        self.list.scrollToBottom()
