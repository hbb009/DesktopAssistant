# pages/shared_log_panel.py
# 「音频处理」页声音卡右边的共用记录面板：AUK 与 CosVoice3 写进同一条时间线。
#
# 写法照搬视频下载页的运行日志（pages/page_douyin.py 的 _log / _scroll_log_bottom），
# 那套已经踩过两个坑：
#   1. 追加内容后 maximum() 还没被文档布局更新，直接 setValue(maximum()) 设的是
#      「上一次」的最大值，日志会卡在中间不动 → 改用 moveCursor(End) +
#      ensureCursorVisible() 走文档坐标，再补一次 singleShot(0)。
#   2. 用户手动往上翻时被强行拉回底部 → 先判断是不是本来就停在底部。
# 加上时间戳与分级颜色（ok/err/warn/info），最新一条永远看得见。

from __future__ import annotations

import time

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QTextBlock, QTextCursor
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QTextEdit

from styles.style_all import tk, restyle_card_title

# 面板最多留多少行
_MAX_BLOCKS = 400


class SharedLogPanel(QWidget):
    """两页共用的记录面板：时间戳 + 分级颜色 + 自动跟底。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SharedLogPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        self.title = QLabel("记录")
        self.title.setProperty("role", "card-title")
        restyle_card_title(self.title)
        lay.addWidget(self.title)

        self.box = QTextEdit()
        self.box.setObjectName("SharedLogBox")
        self.box.setReadOnly(True)
        self.box.setAcceptRichText(False)
        self.box.setLineWrapMode(QTextEdit.WidgetWidth)
        self.box.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.box.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.box.document().setDocumentMargin(0)
        lay.addWidget(self.box, 1)

        # replace_prefix -> 那一行当前所在的块
        self._live: dict[str, QTextBlock] = {}

    # ── 写入 ──────────────────────────────────────────────────────────
    def append(self, text: str, level: str = "info", replace_prefix: str = "") -> None:
        """追加一条。replace_prefix 非空时刷新「那一行」，而不是再插一行。"""
        text = " ".join((text or "").split())
        if not text:
            return
        prefix = (replace_prefix or "").strip()
        sb = self.box.verticalScrollBar()
        # 用户手动往上翻时不要强拉回底部
        follow = sb.value() >= sb.maximum() - 8
        blk = self._live.get(prefix) if prefix else None
        if blk is not None:
            if blk.isValid():
                self._rewrite(blk, text, level)
                if follow:
                    self._scroll_bottom()
            else:
                self._live.pop(prefix, None)  # 那行已经被裁掉了
                blk = None
        if blk is None:
            blk = self._write(text, level)
            if prefix:
                self._live[prefix] = blk
            if follow:
                self._scroll_bottom()
        # ok/err 是终态：这一行就地变成结果，之后不再被进度覆盖
        if prefix and level in ("ok", "err"):
            self._live.pop(prefix, None)

    def _write(self, text: str, level: str):
        self.box.append(self._html(text, level))
        self._trim()
        return self.box.document().lastBlock()

    def _rewrite(self, block, text: str, level: str) -> None:
        """整块替换，保持它在原地不动。"""
        cur = QTextCursor(block)
        cur.movePosition(QTextCursor.StartOfBlock)
        cur.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
        cur.removeSelectedText()
        cur.insertHtml(self._html(text, level))

    @staticmethod
    def _html(text: str, level: str) -> str:
        colors = {
            "ok": tk("ok"),
            "err": tk("err"),
            "warn": tk("warn"),
            "info": tk("text_mut"),
        }
        color = colors.get(level, tk("text_mut"))
        ts = time.strftime("%H:%M:%S")
        return (
            f'<span style="color:{tk("text_faint")}">[{ts}]</span> '
            f'<span style="color:{color}">{text}</span>'
        )

    def _trim(self) -> None:
        doc = self.box.document()
        if doc.blockCount() <= _MAX_BLOCKS:
            return
        cur = QTextCursor(doc)
        cur.beginEditBlock()
        cur.setPosition(0)
        for _ in range(doc.blockCount() - _MAX_BLOCKS):
            if not cur.movePosition(QTextCursor.NextBlock):
                break
        cur.removeSelectedText()
        cur.endEditBlock()
        # 被裁掉的块已经失效，从 live 表里清掉
        for k, b in list(self._live.items()):
            if not b.isValid():
                self._live.pop(k, None)

    def clear(self) -> None:
        self.box.clear()
        self._live.clear()

    def _scroll_bottom(self) -> None:
        """滚到最新一行。走文档坐标，不依赖滚动条最大值（见模块头注释）。"""
        self.box.moveCursor(QTextCursor.End)
        self.box.ensureCursorVisible()
        sb = self.box.verticalScrollBar()
        sb.setValue(sb.maximum())
        QTimer.singleShot(0, lambda: sb.setValue(sb.maximum()))

    # ── 主题 ──────────────────────────────────────────────────────────
    def refresh_theme(self, *_):
        restyle_card_title(self.title)
        self.box.setStyleSheet(
            "QTextEdit#SharedLogBox{"
            "background:transparent;border:none;padding:0;"
            f"color:{tk('text_mut')};font-size:12px;}}"
            "QTextEdit#SharedLogBox QScrollBar:vertical{width:6px;}"
        )
