# pages/page_cosvoice3.py
# 「音频处理」页的 CosVoice3 页签：就是原来的语音克隆面板（PageVoiceClone）。
#
# v9.18 之前它内嵌在「语音录入」页右半列，和左边的转写记录共用操作记录、
# 播放选中与波形条。拆成独立页签后按需求全部断开：
#   · 操作记录 —— 本页自带一块，不再回写语音录入页
#   · 播放     —— 只播共享区里选中的声音卡
#   · 波形条   —— 只跟本页自己的播放器走
# 业务逻辑一行没动，全部继承；这里只补记录区和页签背景。
#
# 声音卡带子 + 播放行（播放按钮/波形/状态灯/提示）由「音频处理」容器创建，
# 通过 shared_host 传进来，落在 AUK 与 CosVoice3 两个页签上方共用。

from __future__ import annotations

from PyQt5.QtCore import Qt, QSize, QTimer
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QFrame,
    QSizePolicy,
    QAbstractItemView,
)

from styles.style_all import (
    tk,
    apply_simple_record,
    restyle_card_title,
)
from pages.page_voice_clone import PageVoiceClone

# 操作记录保留行数（与语音录入页一致）
_OP_LOG_ROWS = 6
_OP_LOG_MAX = 80


def _apply_tab_page_bg(widget, object_name: str):
    """页签内容底色：跟视频下载 / 音频下载的子页同一套。"""
    widget.setObjectName(object_name)
    widget.setAttribute(Qt.WA_StyledBackground, True)
    widget.setAutoFillBackground(True)
    bg = tk("panel_2")
    widget.setStyleSheet(f"#{object_name}{{background:{bg};border:none;}}")


class PageCosVoice3(PageVoiceClone):
    """语音克隆独立页：继承全部功能，外挂自带「操作记录」区。

    声音卡 + 播放行不在本页里，由「音频处理」容器通过 shared_host 放到页签上方，
    AUK 页签共用同一批卡。
    """

    def __init__(self, parent=None, shared_host=None, result_host=None,
                 card_host=None, log_host=None, shared_log=None):
        super().__init__(parent, shared_host=shared_host, result_host=result_host,
                         card_host=card_host, log_host=log_host, shared_log=shared_log)
        self._op_log_row_h = 0
        if getattr(self, "_shared_log", None) is not None:
            # 共用记录面板接管：不再建本页那块
            self._log_row = None
            self._extra_log_row = None
            self._log_external = True
        else:
            self._build_op_log_row()
            # 记录区自己接日志：父类 _op_log 会写到这里
            self.set_op_log_callback(self._log)
        self._apply_column_heights()

    def log_widget(self):
        """本页记录区控件。接了共用面板时为 None。"""
        return getattr(self, "_log_row", None)

    # ── 操作记录区（左下）──────────────────────────────────────────────
    def _build_op_log_row(self):
        """左下角追加：分隔线 + 「操作记录」标题 + 记录列表。

        容器注入了共用记录面板时，本页不再建这一块——AUK 与 CosVoice3 写进
        面板外那一条时间线。
        """
        if getattr(self, "_shared_log", None) is not None:
            self._log_row = None
            self._extra_log_row = None
            self._log_external = True
            return
        row = QWidget()
        row.setObjectName("CosVoice3LogRow")
        row.setAttribute(Qt.WA_StyledBackground, True)
        row.setStyleSheet("#CosVoice3LogRow{background:transparent;border:none;}")
        lay = QVBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        sep = QFrame()
        sep.setObjectName("CosVoice3LogSep")
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"#CosVoice3LogSep{{background:{tk('border')};border:none;}}")
        self._log_sep = sep
        lay.addWidget(sep)

        head = QLabel("操作记录")
        head.setProperty("role", "card-title")
        restyle_card_title(head)
        # 关键：QLabel 默认纵向可伸展，标题会把分隔线下面的空隙全吃掉（实测 94px）。
        # 锁成 Fixed，内容才会贴住分隔线，其余空隙留在底部。
        head.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._log_title = head
        lay.addWidget(head)

        self.log = QListWidget()
        self.log.setObjectName("CosVoice3OpLog")
        self.log.setWordWrap(False)
        self.log.setTextElideMode(Qt.ElideRight)
        self.log.setSpacing(0)
        self.log.setSelectionMode(QAbstractItemView.NoSelection)
        self.log.setFocusPolicy(Qt.NoFocus)
        self.log.setUniformItemSizes(True)
        lay.addWidget(self.log, 0)
        lay.addStretch(1)

        # 记录区：容器给了 log_host 就进共享记录面板（声音卡右边），
        # 否则留在本页左下
        if self._log_host is not None:
            host = self._log_host.layout()
            if host is None:
                host = QVBoxLayout(self._log_host)
                host.setContentsMargins(0, 0, 0, 0)
                host.setSpacing(0)
            host.addWidget(row, 1)
            self._log_external = True
        else:
            host = getattr(self, "_col_left_lay", None)
            if host is not None:
                host.addWidget(row, 0)
                self._log_external = False
            else:
                self.layout().addWidget(row, 0)
                self._log_external = False
        self._log_row = row
        # 记录区自然高度，剩余留给编辑区
        self._extra_log_row = row
        self._style_op_log()

    def log_widget(self):
        """供容器放进共享记录面板并按页签切换。"""
        return getattr(self, "_log_row", None)

    def _log_block_height(self) -> int:
        """记录区整块高度：分隔线 + 标题 + 列表 + 三个间距。

        两个坑：
        · 不取 row.sizeHint()：刚挂进布局时它还是旧的，会把编辑区吃满。
        · 固定高的控件取 height() 而不是 sizeHint()：QListWidget 的 sizeHint
          按「条目数 × 行高」算（有 10 条就是 192），不是可见高度 108。
        """
        parts = []
        for w in (getattr(self, "_log_sep", None),
                  getattr(self, "_log_title", None),
                  getattr(self, "log", None)):
            if w is None:
                continue
            try:
                if w.minimumHeight() == w.maximumHeight():
                    parts.append(int(w.height()))
                else:
                    parts.append(int(w.sizeHint().height()))
            except Exception:
                pass
        if not parts:
            return 0
        sp = 0
        row = getattr(self, "_log_row", None)
        if row is not None and row.layout() is not None:
            sp = max(0, int(row.layout().spacing()))
        # 3 个控件之间 2 段间距，末尾那个 addStretch 不占高度
        return sum(parts) + sp * (len(parts) - 1)

    def _apply_column_heights(self):
        """记录区在共享面板时，编辑区直接吃满；还在本页左下才做两段分配。"""
        if getattr(self, "_log_external", False):
            super()._apply_column_heights()
            return
        text = getattr(self, "_col_text", None)
        lay = getattr(self, "_col_left_lay", None)
        if text is None or lay is None:
            super()._apply_column_heights()
            return
        total = self._page_body_height()
        if total < 120:
            return
        # 先放开旧的固定高，否则窗口缩小时列高被旧值顶住收不回来
        try:
            text.setMinimumHeight(0)
            text.setMaximumHeight(16777215)
        except Exception:
            pass
        self._col_layout_busy = True
        try:
            hist = getattr(self, "_col_hist", None)
            if hist is not None and (
                hist.minimumHeight() != total or hist.maximumHeight() != total
            ):
                hist.setFixedHeight(total)
            # 记录区按自然高度（分隔线 + 标题 + 6 行），剩余全给编辑区
            row = getattr(self, "_log_row", None)
            log_h = self._log_block_height()
            if row is not None and log_h > 0 and (
                row.minimumHeight() != log_h or row.maximumHeight() != log_h
            ):
                row.setFixedHeight(log_h)
            spacing = max(0, int(lay.spacing()))
            text_h = total - log_h - spacing
            if text_h >= 100 and (
                text.minimumHeight() != text_h or text.maximumHeight() != text_h
            ):
                text.setFixedHeight(text_h)
        finally:
            self._col_layout_busy = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._apply_column_heights)

    def _style_op_log(self):
        """单行、左边不留选中条空位；高度刚好 N 行，超出自动滚屏。"""
        lg = getattr(self, "log", None)
        if lg is None:
            return
        apply_simple_record(lg)
        lg.setWordWrap(False)
        lg.setSpacing(0)
        lg.setFrameShape(QFrame.NoFrame)
        lg.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        lg.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        try:
            lg.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        except Exception:
            pass
        try:
            lg.setStyleSheet(
                "QListWidget#CosVoice3OpLog{"
                "background:transparent;border:none;padding:0;}"
                "QListWidget#CosVoice3OpLog::item{"
                "background:transparent;border:none;margin:0;"
                "padding:0px 4px 0px 0px;}"
                "QListWidget#CosVoice3OpLog::item:hover,"
                "QListWidget#CosVoice3OpLog::item:selected,"
                "QListWidget#CosVoice3OpLog::item:selected:active,"
                "QListWidget#CosVoice3OpLog::item:selected:!active{"
                "background:transparent;border:none;}"
            )
        except Exception:
            pass
        fm = lg.fontMetrics()
        row_h = max(int(fm.lineSpacing()) + 2, 18)
        self._op_log_row_h = row_h
        lg.setFixedHeight(row_h * _OP_LOG_ROWS)
        for i in range(lg.count()):
            it = lg.item(i)
            if it is not None:
                it.setSizeHint(QSize(10, row_h))
        # 记录区高度变了：让左列重排 + 两栏重算
        row = getattr(self, "_log_row", None)
        if row is not None:
            row.updateGeometry()
            lay = getattr(self, "_col_left_lay", None)
            if lay is not None:
                lay.invalidate()
        self._apply_column_heights()

    def _log(self, text: str, replace_prefix: str = ""):
        """本页操作记录。同前缀的行原地替换，否则追加并贴底。"""
        text = (text or "").strip()
        if not text:
            return
        lg = getattr(self, "log", None)
        if lg is None:
            return
        prefix = (replace_prefix or "").strip()
        if prefix:
            for i in range(lg.count() - 1, -1, -1):
                it = lg.item(i)
                if it is not None and it.text().startswith(prefix):
                    it.setText(text)
                    self._scroll_op_log()
                    return
        item = QListWidgetItem(text)
        row_h = int(getattr(self, "_op_log_row_h", 0) or 0)
        if row_h > 0:
            item.setSizeHint(QSize(10, row_h))
        lg.addItem(item)
        while lg.count() > _OP_LOG_MAX:
            lg.takeItem(0)
        self._scroll_op_log()

    def _scroll_op_log(self):
        """新记录贴底；等布局刷新后再滚一次，避免刚写入时条还没长出来。"""
        lg = getattr(self, "log", None)
        if lg is None:
            return

        def _to_end():
            lg.scrollToBottom()
            bar = lg.verticalScrollBar()
            if bar is not None:
                bar.setValue(bar.maximum())

        _to_end()
        QTimer.singleShot(0, _to_end)

    # ── 主题 / 页签背景 ───────────────────────────────────────────────
    def refresh_theme(self, *_):
        super().refresh_theme(*_)
        title = getattr(self, "_log_title", None)
        if title is not None:
            restyle_card_title(title)
        sep = getattr(self, "_log_sep", None)
        if sep is not None:
            sep.setStyleSheet(f"#CosVoice3LogSep{{background:{tk('border')};border:none;}}")
        self._style_op_log()

    def apply_tab_bg(self, object_name: str = "TabCosVoice3"):
        _apply_tab_page_bg(self, object_name)
        self._apply_column_heights()
