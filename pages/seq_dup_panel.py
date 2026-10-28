# pages/seq_dup_panel.py
# 序列文件检查 + 查重功能面板（从速存图文迁到文本编辑）
# 偏好仍落在 user.txt → fast_save 段。

from __future__ import annotations

import os
import re

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QSizePolicy, QFileDialog, QLineEdit,
)

from styles.style_all import (
    install_card_title, restyle_card_title, restyle_card_frame,
    apply_btn_download, message_box_info, message_box_warn,
    theme, tk, CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP,
)
from utils.cursor_toast import show_cursor_toast


class SeqDupPanel(QFrame):
    """文件处理：上半序列检查，下半查重（紧凑布局）。"""

    prefs_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CardTextSeqDup")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._theme_titles = []
        self._theme_frames = [self]
        self._h_seps = []
        self._check_folder = ""
        self._dup_worker = None
        self._dup_dialog = None
        self._dup_multi_dialog = None
        self._build_ui()
        try:
            theme.changed.connect(self.refresh_theme)
        except Exception:
            pass

    @staticmethod
    def _typo(w, name):
        w.setProperty("typo", name)
        try:
            w.style().unpolish(w)
            w.style().polish(w)
        except Exception:
            pass

    def _restyle_hseps(self):
        # 占位高度仍用 setFixedHeight(2)，只把可见线画成 1px（同文本操作 hsep）
        color = tk("border")
        qss = (
            "QFrame#TextSeqDupHSep{"
            f"background:transparent;border:none;border-top:1px solid {color};"
            f"max-height:2px;min-height:2px;}}"
        )
        for sep in getattr(self, "_h_seps", []) or []:
            sep.setStyleSheet(qss)

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
        self._restyle_hseps()
        for name in ("dup_ext_bar", "dup_multi_drop"):
            w = getattr(self, name, None)
            if w is not None and hasattr(w, "refresh_theme"):
                try:
                    w.refresh_theme()
                except Exception:
                    pass

    def _build_ui(self):
        # 顶对齐：底部拖放区 stretch=1 占满剩余高度，上方内容保持自然高度堆叠
        check_box = QVBoxLayout(self)
        check_box.setContentsMargins(8, 4, 8, 4)
        check_box.setSpacing(4)
        check_box.setAlignment(Qt.AlignTop)

        title = install_card_title(self, check_box, "文件处理")
        self._theme_titles.append(title)

        def _style_action_btn(btn: QPushButton) -> None:
            apply_btn_download(btn)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
            # 窄栏里默认左右 padding 过大易裁字
            btn.setStyleSheet(
                (btn.styleSheet() or "")
                + "\nQPushButton#BtnDownload{padding:3px 10px; font-size:12px;}"
            )
            try:
                from PyQt5.QtGui import QFontMetrics
                fm = QFontMetrics(btn.font())
                need = int(fm.horizontalAdvance(btn.text()) + 28)
                btn.setMinimumWidth(max(need, 72))
            except Exception:
                pass

        # —— 上：序列 ——
        row_seq_opts = QHBoxLayout()
        row_seq_opts.setContentsMargins(0, 0, 0, 0)
        row_seq_opts.setSpacing(6)
        lbl_seq = QLabel("序列")
        lbl_seq.setStyleSheet("background:transparent;font-size:11px;")
        self._typo(lbl_seq, "muted")
        row_seq_opts.addWidget(lbl_seq, 0, Qt.AlignVCenter)

        self.check_path = QLineEdit()
        self.check_path.setVisible(False)

        self.chk_ignore_ext = QCheckBox("忽略扩展名")
        self.chk_ignore_ext.setStyleSheet("background:transparent; font-size:11px; spacing:0;")
        self.chk_ignore_ext.setChecked(True)
        self.chk_ignore_ext.setToolTip("勾选后，不同扩展名但编号同属一列也合并检查")
        self.chk_ignore_ext.toggled.connect(
            lambda: self._check_scan() if self._check_folder else None
        )
        row_seq_opts.addWidget(self.chk_ignore_ext, 0)

        self.chk_check_fill = QCheckBox("补全")
        self.chk_check_fill.setStyleSheet("background:transparent; font-size:11px; spacing:0;")
        self.chk_check_fill.setChecked(False)
        self.chk_check_fill.setToolTip(
            "仅 e-hentai.org：指定文件夹时统计缺失，读取 _缺失文件清单.txt 源链接，\n"
            "转入图集「e-hentai.org」页只补序列检查得到的真实缺失。"
        )
        row_seq_opts.addWidget(self.chk_check_fill, 0)
        row_seq_opts.addStretch(1)
        check_box.addLayout(row_seq_opts)

        self.btn_check_browse = QPushButton("指定文件夹")
        _style_action_btn(self.btn_check_browse)
        self.btn_check_browse.setToolTip("选择文件夹，检查编号是否连续（如 01、02、04…）")
        self.btn_check_browse.clicked.connect(self._check_browse_folder)
        check_box.addWidget(self.btn_check_browse, 0, Qt.AlignLeft)

        self.lbl_check_result = QLabel("")
        self.lbl_check_result.setWordWrap(True)
        self.lbl_check_result.setStyleSheet(
            "background:transparent; font-size:12px; color:#94a3b8; padding-top:2px;"
        )
        self.lbl_check_result.setVisible(False)
        check_box.addWidget(self.lbl_check_result)

        sep_dup = QFrame()
        sep_dup.setObjectName("TextSeqDupHSep")
        sep_dup.setFrameShape(QFrame.NoFrame)
        sep_dup.setFixedHeight(2)
        sep_dup.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        sep_dup.setAttribute(Qt.WA_StyledBackground, True)
        self._h_seps.append(sep_dup)
        self._restyle_hseps()
        check_box.addWidget(sep_dup)

        # —— 下：查重 ——
        row_dup_head = QHBoxLayout()
        row_dup_head.setContentsMargins(0, 0, 0, 0)
        row_dup_head.setSpacing(6)
        lbl_dup = QLabel("查重")
        lbl_dup.setStyleSheet("background:transparent;font-size:11px;")
        self._typo(lbl_dup, "muted")
        row_dup_head.addWidget(lbl_dup, 0, Qt.AlignVCenter)
        self.lbl_dup_hint = QLabel(self._dup_idle_hint())
        self.lbl_dup_hint.setWordWrap(False)
        self.lbl_dup_hint.setStyleSheet("background:transparent;font-size:11px;")
        self._typo(self.lbl_dup_hint, "muted")
        self.lbl_dup_hint.setToolTip(self._dup_clean_tooltip())
        row_dup_head.addWidget(self.lbl_dup_hint, 1)
        check_box.addLayout(row_dup_head)

        from pages.page_dup_clean import ExtFilterBar
        self.dup_ext_bar = ExtFilterBar()
        self.dup_ext_bar.changed.connect(self._dup_exts_changed)
        check_box.addWidget(self.dup_ext_bar)

        row_dup_btns = QHBoxLayout()
        row_dup_btns.setContentsMargins(0, 0, 0, 0)
        row_dup_btns.setSpacing(6)
        self.btn_dup_multi = QPushButton("开始")
        _style_action_btn(self.btn_dup_multi)
        self.btn_dup_multi.setToolTip(
            "对下方拖入的目录一起查重。同名看大小，不同名但一样大标为疑似。"
        )
        self.btn_dup_multi.clicked.connect(self._dup_multi_start)
        row_dup_btns.addWidget(self.btn_dup_multi, 0)
        row_dup_btns.addStretch(1)
        check_box.addLayout(row_dup_btns)

        from pages.page_dup_clean import MultiDirDrop
        self.dup_multi_drop = MultiDirDrop()
        self.dup_multi_drop.paths_changed.connect(self._dup_multi_paths_changed)
        self.dup_multi_drop.setMinimumHeight(56)
        self.dup_multi_drop.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.dup_multi_drop.setToolTip(self._dup_clean_tooltip())
        self.dup_multi_drop.setCursor(Qt.PointingHandCursor)
        check_box.addWidget(self.dup_multi_drop, 1)  # stretch=1：占满右栏剩余垂直空间
        self._dup_multi_dialog = None
        self._dup_sync_multi_btn()

        try:
            self.chk_ignore_ext.toggled.connect(lambda *_: self.prefs_changed.emit())
            self.chk_check_fill.toggled.connect(lambda *_: self.prefs_changed.emit())
            self.dup_ext_bar.changed.connect(lambda *_: self.prefs_changed.emit())
        except Exception:
            pass



    def export_settings(self) -> dict:
        return {
            "check_ignore_ext": bool(self.chk_ignore_ext.isChecked()),
            "check_fill": bool(self.chk_check_fill.isChecked()),
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
        if "check_fill" in d:
            self.chk_check_fill.blockSignals(True)
            self.chk_check_fill.setChecked(bool(d.get("check_fill")))
            self.chk_check_fill.blockSignals(False)
        if "dup_exts" in d:
            raw = d.get("dup_exts")
            off = d.get("dup_exts_off")
            self.dup_ext_bar.set_exts(
                raw if isinstance(raw, list) else [],
                off if isinstance(off, list) else [],
            )
            self._dup_refresh_idle_hint()

    def stop_background_checks(self):
        try:
            w = getattr(self, "_dup_worker", None)
            if w is not None:
                w.stop()
            from utils.qthread_util import stop_qthread
            stop_qthread(w, name="dup_clean_scan")
        except Exception:
            pass
        for attr in ("_dup_dialog", "_dup_multi_dialog"):
            dlg = getattr(self, attr, None)
            if dlg is not None:
                try:
                    dlg.shutdown()
                    dlg.close()
                except Exception:
                    pass
                setattr(self, attr, None)

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

    def _dup_ext_list(self) -> list:
        _all, active = self._dup_ext_bar_lists()
        return active

    def _dup_scan_filter(self):
        """None = 全部类型。False = 卡片都临时关了。列表 = 只查这些。"""
        all_exts, active = self._dup_ext_bar_lists()
        if not all_exts:
            return None
        if not active:
            return False
        return active

    def _dup_clean_tooltip(self) -> str:
        all_exts, active = self._dup_ext_bar_lists()
        base = (
            "扫描指定目录下文件与文件夹。\n"
            "文件名相同且字节大小相同的，只保留修改日期最新的一份，较早版本移入回收站。"
        )
        if not all_exts:
            return "无扩展名卡片时处理全部类型。\n" + base
        if not active:
            return "扩展名卡片都已临时关闭，这次不检查文件。\n" + base
        shown = "、".join("." + e for e in active[:8])
        if len(active) > 8:
            shown += f" 等 {len(active)} 种"
        off = [e for e in all_exts if e not in set(active)]
        extra = ""
        if off:
            extra = "\n已临时关闭 " + "、".join("." + e for e in off[:8])
        return f"仅处理 {shown}。{extra}\n" + base

    def _dup_idle_hint(self) -> str:
        all_exts, active = self._dup_ext_bar_lists()
        if not all_exts:
            return "同名同大小，只留最新"
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

    def _dup_refresh_idle_hint(self):
        if getattr(self, "lbl_dup_hint", None) is None:
            return
        self.lbl_dup_hint.setText(self._dup_idle_hint())
        drop = getattr(self, "dup_multi_drop", None)
        if drop is not None:
            try:
                drop.setToolTip(self._dup_clean_tooltip())
            except Exception:
                pass

    def _dup_exts_changed(self):
        self._dup_refresh_idle_hint()

    def _dup_sync_multi_btn(self):
        btn = getattr(self, "btn_dup_multi", None)
        drop = getattr(self, "dup_multi_drop", None)
        if btn is None:
            return
        scanning = False
        w = getattr(self, "_dup_worker", None)
        try:
            scanning = w is not None and w.isRunning()
        except Exception:
            scanning = False
        n = 0
        if drop is not None:
            try:
                n = len(drop.paths())
            except Exception:
                n = 0
        btn.setEnabled((not scanning) and n > 0)

    def _dup_multi_paths_changed(self):
        self._dup_sync_multi_btn()

    def _dup_multi_start(self):
        drop = getattr(self, "dup_multi_drop", None)
        paths = drop.paths() if drop is not None else []
        if not paths:
            return
        w = getattr(self, "_dup_worker", None)
        if w is not None and w.isRunning():
            return
        from pages.page_dup_clean import MultiDupDialog
        dlg = MultiDupDialog(self.window() or self, paths, ext_filter=self._dup_scan_filter())
        self._dup_multi_dialog = dlg
        dlg.start()
        dlg.exec_()
        self._dup_multi_dialog = None
        self._dup_sync_multi_btn()


    def _check_browse_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择要检查的文件夹")
        if path:
            self._check_folder = path
            result = self._check_scan()
            # 勾选「补全」且确有缺失 → 先弹 3 秒分析结果，再转入图集下载
            if (
                result
                and getattr(self, "chk_check_fill", None) is not None
                and self.chk_check_fill.isChecked()
                and int(result.get("total_missing") or 0) > 0
            ):
                if self._check_show_analysis_dialog(result):
                    self._check_start_fill(result)

    def _check_show_analysis_dialog(self, scan_result: dict) -> bool:
        """指定文件夹后的分析结果弹窗（约 3 秒），说明下一步将去 e-hentai 补全。

        返回 True 继续补全；False 用户取消。
        """
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QDialogButtonBox

        folder = (scan_result or {}).get("folder") or ""
        total_missing = int((scan_result or {}).get("total_missing") or 0)
        missing_names = list((scan_result or {}).get("missing_names") or [])
        has_report = bool((scan_result or {}).get("has_report"))
        groups_n = int((scan_result or {}).get("groups_n") or 0)
        range_hint = (scan_result or {}).get("ranges_text") or ""
        expected_total = int((scan_result or {}).get("expected_total") or 0)
        leaf = os.path.basename(folder.rstrip("\\/")) if folder else "（未选）"

        preview = "、".join(missing_names[:8])
        if len(missing_names) > 8:
            preview += f" 等 {len(missing_names)} 个"
        if not preview:
            preview = "（无）"

        dlg = QDialog(self)
        dlg.setWindowTitle("序列检查 · 分析结果")
        dlg.setModal(True)
        dlg.setMinimumWidth(440)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(10)

        title = QLabel("分析完成")
        title.setStyleSheet("font-size:15px; font-weight:700; background:transparent;")
        lay.addWidget(title)

        extra_lines = ""
        if groups_n:
            extra_lines += f"<br><b>序列</b>　{groups_n} 组"
        if range_hint:
            extra_lines += f"<br><b>已有范围</b>　{range_hint}"
        if expected_total > 0:
            extra_lines += f"<br><b>应下总数</b>　{expected_total}（来自清单）"

        body = QLabel(
            f"<b>目录</b>　{leaf}"
            + extra_lines
            + f"<br><b>缺失</b>　<span style='color:#ef4444;font-weight:700;'>{total_missing}</span> 个"
            + f"<br><b>示例</b>　{preview}<br>"
            f"<b>清单</b>　"
            + ("已找到 _缺失文件清单.txt" if has_report else "未找到清单（稍后可粘贴图库链接）")
            + "<br><br>"
            "下一步将转到 <b>图集下载 · e-hentai.org</b>，"
            "定点解析并只下载上述缺失序号。"
        )
        body.setWordWrap(True)
        body.setTextFormat(Qt.RichText)
        body.setStyleSheet("background:transparent; font-size:13px; line-height:1.4;")
        lay.addWidget(body)

        countdown_sec = 3
        lbl_cd = QLabel(f"{countdown_sec} 秒后自动开始补全…")
        lbl_cd.setStyleSheet("background:transparent; color:#94a3b8; font-size:12px;")
        lay.addWidget(lbl_cd)

        btn_box = QDialogButtonBox()
        btn_go = btn_box.addButton("立即开始", QDialogButtonBox.AcceptRole)
        btn_cancel = btn_box.addButton("取消", QDialogButtonBox.RejectRole)
        btn_go.setDefault(True)
        lay.addWidget(btn_box)

        state = {"left": countdown_sec, "done": False}

        def _tick():
            if state["done"]:
                return
            state["left"] -= 1
            if state["left"] <= 0:
                state["done"] = True
                timer.stop()
                dlg.accept()
                return
            lbl_cd.setText(f"{state['left']} 秒后自动开始补全…")
            btn_go.setText(f"立即开始（{state['left']}）")

        timer = QTimer(dlg)
        timer.setInterval(1000)
        timer.timeout.connect(_tick)
        btn_go.setText(f"立即开始（{countdown_sec}）")

        def _accept():
            state["done"] = True
            timer.stop()
            dlg.accept()

        def _reject():
            state["done"] = True
            timer.stop()
            dlg.reject()

        btn_box.accepted.connect(_accept)
        btn_box.rejected.connect(_reject)
        timer.start()

        ok = dlg.exec_() == QDialog.Accepted
        timer.stop()
        return bool(ok)

    def _set_check_result(self, text: str, rich: bool = False):
        lab = getattr(self, "lbl_check_result", None)
        if lab is None:
            return
        t = text or ""
        lab.setText(t)
        if rich:
            lab.setTextFormat(Qt.RichText)
        lab.setVisible(bool(t.strip()))

    def _check_scan(self):
        """扫描编号序列缺失。返回 dict 或 None（失败/无序列）。"""
        folder = getattr(self, "_check_folder", "") or ""
        if not folder or not os.path.isdir(folder):
            self._set_check_result("请先选择文件夹")
            return None

        try:
            all_files = os.listdir(folder)
        except Exception as e:
            self._set_check_result(f"读取失败: {e}")
            return None

        files = [f for f in all_files if os.path.isfile(os.path.join(folder, f))]

        if not files:
            self._set_check_result("文件夹内无文件")
            return None

        ignore_ext = self.chk_ignore_ext.isChecked()
        groups = {}
        unnumbered = []
        for f in files:
            name, ext = os.path.splitext(f)
            m = re.search(r'(\d+)$', name)
            if m:
                prefix = name[:m.start()]
                num = int(m.group(1))
                digits = m.group(1)
                key = (prefix.lower(), ext.lower()) if not ignore_ext else (prefix.lower(),)
                groups.setdefault(key, []).append((num, digits, f))
            else:
                unnumbered.append(f)

        if not groups:
            self._set_check_result("未检测到编号序列")
            return None

        # 清单「应下：N」→ 把序列范围扩到 1..N（否则尾部整段缺失扫不出来）
        expected_total = 0
        try:
            from pages.page_gallery import find_missing_report, parse_missing_report
            _rp = find_missing_report(folder)
            if _rp:
                _info = parse_missing_report(_rp)
                expected_total = int(_info.get("expected_count") or 0)
        except Exception:
            expected_total = 0

        lines = []
        total_missing = 0
        all_missing_nums = []

        for key, items in groups.items():
            items.sort(key=lambda x: x[0])
            nums = [it[0] for it in items]
            num_min, num_max = nums[0], nums[-1]
            zfill = max(len(it[1]) for it in items)
            if ignore_ext:
                prefix = key[0]
            else:
                prefix, _ext = key
                prefix = prefix or ""
            # EH 风格纯数字序号：有「应下」时从 1 扫到应下总数
            is_pure_num = (not prefix) or (str(prefix).strip() == "")
            if expected_total > 0 and is_pure_num:
                num_min = 1
                num_max = max(num_max, expected_total)
                zfill = max(zfill, len(str(num_max)), 4)
            missing = sorted(set(range(num_min, num_max + 1)) - set(nums))
            total_missing += len(missing)
            for n in missing:
                all_missing_nums.append(f"{prefix}{str(n).zfill(zfill)}")

        # Line 1：X 组序列
        lines.append(f"{len(groups)} 组序列")

        # Line 2：范围
        ranges = []
        for key, items in groups.items():
            items.sort(key=lambda x: x[0])
            nums = [it[0] for it in items]
            zfill = max(len(it[1]) for it in items)
            ranges.append(f"{str(nums[0]).zfill(zfill)}~{str(nums[-1]).zfill(zfill)}")
        lines.append("，".join(ranges))

        # Line 3：缺失
        if total_missing:
            missing_preview = "、".join(all_missing_nums[:5])
            if len(all_missing_nums) > 5:
                missing_preview += f" 等{len(all_missing_nums)}个"
            lines.append(f"<b style='color:#ef4444;'>缺失 {total_missing} 个：{missing_preview}</b>")
        else:
            lines.append("无缺失")

        # 补全提示：目录内是否有图集清单
        try:
            from pages.page_gallery import MISSING_REPORT_NAME, find_missing_report
            has_report = bool(find_missing_report(folder))
        except Exception:
            MISSING_REPORT_NAME = "_缺失文件清单.txt"
            has_report = os.path.isfile(os.path.join(folder, MISSING_REPORT_NAME))
        if getattr(self, "chk_check_fill", None) is not None and self.chk_check_fill.isChecked():
            if has_report:
                lines.append("<span style='color:#22c55e;'>已找到清单 · 可补全</span>")
            else:
                lines.append("<span style='color:#f59e0b;'>未找到清单</span>")

        self._set_check_result("<br>".join(lines), rich=True)
        return {
            "folder": folder,
            "total_missing": total_missing,
            "missing_names": list(all_missing_nums),
            "has_report": has_report,
            "groups_n": len(groups),
            "ranges_text": "，".join(ranges) if ranges else "",
            "expected_total": int(expected_total or 0),
        }

    def _check_ask_report_or_url(self, start_dir: str = "") -> str:
        """找不到清单时：可填 清单路径 / 目录 / 或直接粘贴 e-hentai 图库链接。"""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QDialogButtonBox

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
        from PyQt5.QtWidgets import QMessageBox

        folder = (scan_result or {}).get("folder") or getattr(self, "_check_folder", "") or ""
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
                try:
                    self._set_check_result(
                        (self.lbl_check_result.text() or "")
                        + "<br><span style='color:#94a3b8;'>已取消补全</span>",
                        rich=True,
                    )
                except Exception:
                    pass
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
        pg = getattr(mw, "page_gallery", None) if mw is not None else None
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
            try:
                cur = self.lbl_check_result.text() or ""
                self._set_check_result(
                    cur
                    + f"<br><span style='color:#22c55e;'>"
                    f"已转入 e-hentai · 补 {len(missing_names)} 项（见鼠标旁进度气泡）</span>",
                    rich=True,
                )
            except Exception:
                pass
        else:
            try:
                show_cursor_toast("序列补全", "未能启动", accent="warn")
            except Exception:
                pass
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
        """图集页补全结束后回调：刷新序列检查文案，给用户明确结果。"""
        folder = (folder or "").strip() or getattr(self, "_check_folder", "") or ""
        if folder and os.path.isdir(folder):
            self._check_folder = folder
            try:
                self._check_scan()
            except Exception:
                pass
        head = (summary or "").strip() or ("补全成功" if ok else "补全失败")
        color = "#22c55e" if ok else "#ef4444"
        extra = f"<br><b style='color:{color};'>{head}</b>"
        if still_n > 0:
            extra += f"<br><span style='color:#f59e0b;'>仍缺 {still_n}"
            if planned_n:
                extra += f"/{planned_n}"
            extra += " 项</span>"
        elif detail:
            d = (detail or "").strip().split("\n")[0][:80]
            if d:
                extra += f"<br><span style='color:#94a3b8;'>{d}</span>"
        try:
            cur = self.lbl_check_result.text() or ""
            self._set_check_result(cur + extra, rich=True)
        except Exception:
            pass
        # 进度气泡已由 e-hentai 补全流程关闭；此处不再弹独立小气泡，避免叠两层
