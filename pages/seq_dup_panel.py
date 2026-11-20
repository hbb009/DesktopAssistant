# pages/seq_dup_panel.py
# 序列文件检查 + 查重功能面板（从速存图文迁到文本编辑）
# 偏好仍落在 user.txt → fast_save 段。

from __future__ import annotations

import os

from PyQt5.QtCore import Qt, pyqtSignal, QRectF, QPointF
from PyQt5.QtGui import QPainter, QColor, QPen, QBrush
from PyQt5.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QSizePolicy, QFileDialog, QLineEdit, QToolButton, QDialog, QDialogButtonBox, QMenu,
    QGraphicsOpacityEffect,
)

from styles.style_all import (
    install_card_title, restyle_card_title, restyle_card_frame,
    apply_mini_button, message_box_info, message_box_warn, theme, tk,
)
from utils.cursor_toast import show_cursor_toast


class _DupClearBtn(QToolButton):
    """从检查列表清除所有已选文件夹。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SeqDupClearBtn")
        self.setCursor(Qt.ArrowCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip("清空已选文件夹")
        self.setAccessibleName("清空已选文件夹")
        self.setAutoRaise(True)
        self.setEnabled(False)

    def enterEvent(self, e):
        super().enterEvent(e)
        self.update()

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        try:
            bd = QColor(tk("border"))
            acc = QColor(tk("accent"))
            fg = QColor(tk("text") if self.isEnabled() else tk("text_mut"))
        except Exception:
            bd = QColor("#25345c")
            acc = QColor("#3a8ee0")
            fg = QColor("#d7def7") if self.isEnabled() else QColor("#6f7fa8")
        if not self.isEnabled():
            bd.setAlpha(90)
            fg.setAlpha(120)
        hover = self.underMouse() and self.isEnabled()
        rect = QRectF(0.5, 0.5, w - 1.0, h - 1.0)
        if hover:
            fill = QColor(acc)
            fill.setAlpha(40)
            p.setBrush(QBrush(fill))
            p.setPen(QPen(acc, 1))
        else:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(bd, 1))
        p.drawRoundedRect(rect, 6.0, 6.0)
        m = min(w, h) * 0.30
        p.setPen(QPen(fg, 1.8, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(m, m), QPointF(w - m, h - m))
        p.drawLine(QPointF(w - m, m), QPointF(m, h - m))
        p.end()


class SeqDupPanel(QFrame):
    """文件处理入口：根据所选模式检查缺号或查重。"""

    prefs_changed = pyqtSignal()
    dup_status_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CardTextSeqDup")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._theme_titles = []
        self._theme_frames = [self]
        self._dup_multi_dialog = None
        self._last_dup_status = ""
        self._build_ui()
        try:
            theme.changed.connect(self.refresh_theme)
        except Exception:
            pass

    def refresh_theme(self, *_):
        for t in self._theme_titles:
            try:
                restyle_card_title(t)
            except Exception:
                pass
        for c in self._theme_frames:
            try:
                restyle_card_frame(c)
            except Exception:
                pass
        for name in ("dup_ext_bar", "dup_multi_drop"):
            w = getattr(self, name, None)
            if w is not None and hasattr(w, "refresh_theme"):
                try:
                    w.refresh_theme()
                except Exception:
                    pass
        btn = getattr(self, "btn_dup_clear", None)
        if btn is not None:
            btn.update()
        self._restyle_start_button()

    def _restyle_start_button(self):
        self.btn_dup_multi.setStyleSheet(
            f"QPushButton#SeqDupStart{{border:1px solid {tk('text_dim')};border-radius:6px;}}"
            "QPushButton#SeqDupStart:enabled{background:#f0a542;border-color:#f0a542;color:#241608;}"
            "QPushButton#SeqDupStart:hover:enabled,"
            "QPushButton#SeqDupStart:focus:enabled{background:#f7bb64;border-color:#f7bb64;}"
            "QPushButton#SeqDupStart:pressed:enabled{background:#dd932f;border-color:#dd932f;}"
        )

    def _build_ui(self):
        check_box = QVBoxLayout(self)
        check_box.setContentsMargins(8, 4, 8, 4)
        check_box.setSpacing(4)
        check_box.setAlignment(Qt.AlignTop)

        self._check_title = install_card_title(self, check_box, "序列检查")
        self._theme_titles.append(self._check_title)
        from pages.page_dup_clean import ExtFilterBar, MultiDirDrop

        self._settings_dialog = QDialog(self)
        self._settings_dialog.setWindowTitle("文件检查设置")
        settings = QVBoxLayout(self._settings_dialog)
        self.chk_ignore_ext = QCheckBox("查缺号时忽略扩展名")
        self.chk_ignore_ext.setChecked(True)
        settings.addWidget(self.chk_ignore_ext)
        settings_buttons = QDialogButtonBox(QDialogButtonBox.Close)
        settings_buttons.button(QDialogButtonBox.Close).setText("关闭")
        settings_buttons.rejected.connect(self._settings_dialog.reject)
        settings.addWidget(settings_buttons)

        self.btn_dup_multi = apply_mini_button(QPushButton("开始"))
        self.btn_dup_multi.setObjectName("SeqDupStart")
        self.btn_dup_multi.setFixedHeight(22)
        self._restyle_start_button()
        self.btn_dup_multi.clicked.connect(self._dup_multi_start)
        self.btn_dup_clear = _DupClearBtn()
        self.btn_dup_clear.setFixedSize(22, 22)
        self.btn_dup_clear.clicked.connect(self._dup_clear_folders)

        self.chk_dup = QCheckBox("查重")
        self.chk_dup.setStyleSheet("background:transparent; font-size:11px; spacing:3px;")
        self.chk_dup.setCursor(Qt.PointingHandCursor)
        self.chk_dup.setToolTip("勾选时查重并启用文件类型筛选。取消时检查文件名缺号。")
        title_layout = self._check_title.parentWidget().layout()
        title_layout.removeWidget(self._check_title)
        header = QHBoxLayout()
        header.setSpacing(4)
        header.addWidget(self._check_title, 0, Qt.AlignVCenter)
        header.addStretch()
        header.addWidget(self.chk_dup, 0, Qt.AlignVCenter)
        header.addWidget(self.btn_dup_multi, 0, Qt.AlignVCenter)
        header.addWidget(self.btn_dup_clear, 0, Qt.AlignVCenter)
        title_layout.addLayout(header)

        self.dup_multi_drop = MultiDirDrop()
        self.dup_multi_drop.setAccessibleName("添加要检查的文件夹")
        self.dup_multi_drop.paths_changed.connect(self._dup_sync_multi_btn)
        self.dup_multi_drop.setContextMenuPolicy(Qt.CustomContextMenu)
        self.dup_multi_drop.customContextMenuRequested.connect(self._show_folder_menu)
        self.dup_multi_drop.setMinimumHeight(56)
        self.dup_multi_drop.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.dup_multi_drop.setCursor(Qt.PointingHandCursor)
        check_box.addWidget(self.dup_multi_drop, 1)

        self.dup_ext_bar = ExtFilterBar()
        opacity = QGraphicsOpacityEffect(self.dup_ext_bar)
        opacity.setOpacity(0.45)
        self.dup_ext_bar.setGraphicsEffect(opacity)
        check_box.addWidget(self.dup_ext_bar, 1)
        self._sync_check_mode()

        self.chk_ignore_ext.toggled.connect(lambda *_: self.prefs_changed.emit())
        self.chk_dup.toggled.connect(self._sync_check_mode)
        self.chk_dup.toggled.connect(lambda *_: self.prefs_changed.emit())
        self.dup_ext_bar.changed.connect(self._refresh_dup_filter)
        self.dup_ext_bar.changed.connect(self.prefs_changed)

    def export_settings(self) -> dict:
        return {
            "check_ignore_ext": bool(self.chk_ignore_ext.isChecked()),
            "check_fill": False,
            "dup_check_enabled": self.chk_dup.isChecked(),
            "dup_exts": list(self.dup_ext_bar.exts()),
            "dup_exts_off": list(self.dup_ext_bar.off_exts()),
        }

    def apply_settings(self, d: dict):
        if not isinstance(d, dict):
            return
        if "check_ignore_ext" in d:
            self.chk_ignore_ext.blockSignals(True)
            self.chk_ignore_ext.setChecked(bool(d.get("check_ignore_ext")))
            self.chk_ignore_ext.blockSignals(False)
        if "dup_check_enabled" in d:
            self.chk_dup.blockSignals(True)
            self.chk_dup.setChecked(bool(d["dup_check_enabled"]))
            self.chk_dup.blockSignals(False)
        if "dup_exts" in d:
            raw = d.get("dup_exts")
            off = d.get("dup_exts_off")
            self.dup_ext_bar.set_exts(
                raw if isinstance(raw, list) else [],
                off if isinstance(off, list) else [],
            )
        self._sync_check_mode()

    def _sync_check_mode(self):
        check_dup = self.chk_dup.isChecked()
        self._check_title.setText("文件查重" if check_dup else "序列检查")
        self.dup_ext_bar.setEnabled(check_dup)
        self.dup_ext_bar.graphicsEffect().setEnabled(not check_dup)
        self.btn_dup_multi.setToolTip(
            "添加至少一个类型卡后，按下方文件类型查重。"
            if check_dup else "检查各文件夹中同一组文件名的编号是否缺失。"
        )
        self._refresh_dup_filter()

    def stop_background_checks(self):
        dialog = self._dup_multi_dialog
        if dialog is not None:
            dialog.close()

    def shutdown(self):
        self.stop_background_checks()

    def _dup_ext_bar_lists(self):
        bar = getattr(self, "dup_ext_bar", None)
        if bar is None:
            return [], []
        try:
            all_exts = list(bar.exts())
        except Exception:
            all_exts = []
        try:
            active = list(bar.active_exts())
        except Exception:
            active = list(all_exts)
        return all_exts, active

    def _dup_scan_filter(self):
        return self.dup_ext_bar.active_exts() or False

    def _dup_clean_tooltip(self) -> str:
        base = "点击文件夹图标或拖入目录。右键可添加文件夹或打开检查设置。"
        if not self.chk_dup.isChecked():
            return "按各文件夹的文件名编号检查缺号。\n" + base
        all_exts, active = self._dup_ext_bar_lists()
        if not all_exts:
            return "请先添加至少一个查重文件类型。\n" + base
        if not active:
            return "查重文件类型均已关闭。\n" + base
        shown = "、".join("." + e for e in active[:8])
        if len(active) > 8:
            shown += f" 等 {len(active)} 种"
        off = [e for e in all_exts if e not in set(active)]
        extra = ""
        if off:
            extra = "\n已临时关闭 " + "、".join("." + e for e in off[:8])
        return f"查重仅比较 {shown}。{extra}\n{base}"

    def _dup_idle_hint(self) -> str:
        all_exts, active = self._dup_ext_bar_lists()
        if not all_exts:
            return "请先添加文件类型"
        if not active:
            return "扩展名都已临时关闭"
        shown = "、".join("." + e for e in active[:6])
        if len(active) > 6:
            shown += f" 等{len(active)}种"
        off = [e for e in all_exts if e not in set(active)]
        if off:
            off_shown = "、".join("." + e for e in off[:4])
            return f"仅 {shown}（已关 {off_shown}）"
        return f"仅 {shown}"

    def _refresh_dup_filter(self):
        self._dup_sync_multi_btn()
        self.dup_multi_drop.setToolTip(self._dup_clean_tooltip())
        if not self.chk_dup.isChecked():
            return
        status = "查重 " + self._dup_idle_hint()
        if status != self._last_dup_status:
            self._last_dup_status = status
            self.dup_status_changed.emit(status)

    def _show_folder_menu(self, point):
        menu = QMenu(self)
        menu.addAction("添加文件夹…", self.dup_multi_drop.pick_directory)
        menu.addAction("检查设置…", self._settings_dialog.exec_)
        menu.exec_(self.dup_multi_drop.mapToGlobal(point))

    def _dup_sync_multi_btn(self):
        paths = self.dup_multi_drop.paths()
        has_paths = bool(paths)
        self.btn_dup_multi.setEnabled(self._can_start_file_check(paths))
        self.btn_dup_clear.setEnabled(has_paths)
        self.btn_dup_clear.setCursor(Qt.PointingHandCursor if has_paths else Qt.ArrowCursor)

    def _can_start_file_check(self, paths):
        return (
            bool(paths)
            and self._dup_multi_dialog is None
            and (not self.chk_dup.isChecked() or bool(self.dup_ext_bar.exts()))
        )

    def _dup_clear_folders(self):
        self.dup_multi_drop.clear_paths()

    def _dup_multi_start(self):
        paths = self.dup_multi_drop.paths()
        if not self._can_start_file_check(paths):
            return
        from pages.page_dup_clean import MultiDupDialog

        check_dup = self.chk_dup.isChecked()
        dialog = MultiDupDialog(
            self.window(), paths,
            ext_filter=self._dup_scan_filter() if check_dup else None,
            sequence_ignore_ext=None if check_dup else self.chk_ignore_ext.isChecked(),
        )
        self._dup_multi_dialog = dialog
        self._dup_sync_multi_btn()
        try:
            dialog.start()
            dialog.exec_()
            summary = dialog.scan_summary
            fill_request = dialog.fill_request
        finally:
            dialog.shutdown()
            dialog.deleteLater()
            self._dup_multi_dialog = None
            self._dup_sync_multi_btn()
        if summary:
            mode = "查重" if check_dup else "序列检查"
            self.dup_status_changed.emit(f"{mode} · {summary}")
        if fill_request:
            self._check_start_fill(fill_request)

    def _check_ask_report_or_url(self, start_dir: str = "") -> str:
        """找不到清单时：可填 清单路径 / 目录 / 或直接粘贴 e-hentai 图库链接。"""
        try:
            from pages.page_gallery import MISSING_REPORT_NAME
        except Exception:
            MISSING_REPORT_NAME = "_缺失文件清单.txt"

        dlg = QDialog(self)
        dlg.setWindowTitle("补全需要图库链接")
        dlg.setModal(True)
        dlg.setMinimumWidth(460)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(8)
        tip = QLabel(
            f"目录中没有「{MISSING_REPORT_NAME}」（或无法读出链接）。\n"
            "请任选其一：\n"
            "  · 粘贴 e-hentai 图库页链接（形如 https://e-hentai.org/g/…/…/）\n"
            "  · 或指定清单文件 / 原下载目录"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("background:transparent;")
        lay.addWidget(tip)

        row = QHBoxLayout()
        edit = QLineEdit()
        edit.setPlaceholderText("https://e-hentai.org/g/数字/token/  或  清单路径")
        # 不预填目录路径，避免一点确定又走「仍无清单」；优先让用户贴图库链接
        row.addWidget(edit, 1)

        def _browse():
            path, _ = QFileDialog.getOpenFileName(
                dlg,
                "选择缺失文件清单",
                edit.text().strip() or start_dir or "",
                "文本 (*.txt);;所有文件 (*.*)",
            )
            if path:
                edit.setText(path.replace("\\", "/"))
                return
            folder = QFileDialog.getExistingDirectory(
                dlg, "选择原下载目录", edit.text().strip() or start_dir or ""
            )
            if folder:
                edit.setText(folder.replace("\\", "/"))

        btn_browse = QPushButton("浏览…")
        btn_browse.setCursor(Qt.PointingHandCursor)
        btn_browse.clicked.connect(_browse)
        row.addWidget(btn_browse)
        lay.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)

        if dlg.exec_() != QDialog.Accepted:
            return ""
        return (edit.text() or "").strip().strip('"')

    def _check_start_fill(self, scan_result: dict):
        """序列真实缺失 + 图库源链接 → e-hentai 页解析并只补缺失。"""
        folder = scan_result["folder"]
        missing_names = list((scan_result or {}).get("missing_names") or [])
        if not folder or not missing_names:
            return

        try:
            from pages.page_gallery import (
                find_missing_report,
                parse_missing_report,
                extract_eh_gallery_url,
                MISSING_REPORT_NAME,
            )
        except Exception as e:
            message_box_warn(self, "补全失败", f"无法加载图集模块：{e}")
            return

        report_path = find_missing_report(folder)
        source_url = ""
        if report_path:
            info = parse_missing_report(report_path)
            source_url = extract_eh_gallery_url(info.get("source_url") or "") or (
                info.get("source_url") or ""
            ).strip()
            if not source_url and info.get("path"):
                # 再读全文抽一次
                try:
                    with open(info["path"], "r", encoding="utf-8", errors="ignore") as f:
                        source_url = extract_eh_gallery_url(f.read())
                except Exception:
                    pass

        if not source_url:
            user_in = self._check_ask_report_or_url(folder)
            if not user_in:
                self.dup_status_changed.emit("已取消补全")
                return
            # 用户直接贴了图库链接
            source_url = extract_eh_gallery_url(user_in)
            if not source_url:
                # 当路径：找清单
                rp = find_missing_report(user_in)
                if not rp and os.path.isfile(user_in):
                    rp = os.path.abspath(user_in)
                if rp:
                    report_path = rp
                    info = parse_missing_report(rp)
                    source_url = extract_eh_gallery_url(
                        (info.get("source_url") or "") + "\n" + (user_in or "")
                    )
                    if not source_url:
                        try:
                            with open(rp, "r", encoding="utf-8", errors="ignore") as f:
                                source_url = extract_eh_gallery_url(f.read())
                        except Exception:
                            pass
            if not source_url:
                message_box_warn(
                    self,
                    "补全失败",
                    "未能得到 e-hentai 图库链接。\n"
                    "请粘贴形如：\nhttps://e-hentai.org/g/数字/token/\n"
                    f"或提供含该链接的「{MISSING_REPORT_NAME}」。",
                )
                return

        source_url = extract_eh_gallery_url(source_url) or source_url.strip()
        low = source_url.lower()
        if "e-hentai.org" not in low and "exhentai.org" not in low:
            message_box_info(
                self,
                "不支持补全",
                "序列补全仅支持 e-hentai.org（含 exhentai.org）。\n"
                f"当前链接：{source_url[:120]}",
            )
            return
        if "hath.network" in low:
            message_box_warn(
                self,
                "链接类型不对",
                "需要的是图库页链接（…/g/ID/token/），\n"
                "不是清单里 hath.network 的单张图片直链。",
            )
            return

        # 切到图集页并 handoff（固定 e-hentai.org 子页）
        mw = self.window()
        if hasattr(mw, "_ensure_page"):
            mw._ensure_page("gallery")
        pg = getattr(mw, "page_gallery", None)
        if pg is None:
            message_box_warn(self, "补全失败", "未找到图集下载页")
            return
        try:
            if hasattr(mw, "btn_gallery"):
                if hasattr(mw, "_auto_switch"):
                    mw._auto_switch(mw.stack.indexOf(pg), mw.btn_gallery)
                elif hasattr(mw, "_switch"):
                    mw._switch(mw.stack.indexOf(pg), mw.btn_gallery)
        except Exception:
            pass

        ok = False
        try:
            ok = bool(
                pg.handoff_fill_missing(
                    source_url,
                    folder,
                    missing_names,
                    report_path=report_path or "",
                )
            )
        except Exception as e:
            message_box_warn(self, "补全失败", str(e))
            return

        if ok:
            self.dup_status_changed.emit(f"已转入图集下载，补全 {len(missing_names)} 项")
        else:
            show_cursor_toast("序列补全", "未能启动", accent="warn")

    def on_sequence_fill_done(
        self,
        *,
        ok: bool,
        summary: str = "",
        folder: str = "",
        detail: str = "",
        still_n: int = 0,
        planned_n: int = 0,
    ):
        head = (summary or "").strip() or ("补全成功" if ok else "补全失败")
        if still_n > 0:
            tally = f"{still_n}/{planned_n}" if planned_n else str(still_n)
            head += f"，仍缺 {tally} 项"
        elif detail.strip():
            head += f"，{detail.strip().splitlines()[0][:80]}"
        self.dup_status_changed.emit(head)
