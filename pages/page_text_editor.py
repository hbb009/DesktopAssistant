# pages/page_text_editor.py
# 文本编辑 —— 独立整理排版区（语音编辑分离出来的「编辑整理」部分）
# 功能：主编辑区 + 一键整理/智能换行/加序号/加符号前缀 + 前缀符号历史卡片（拖拽排序）

import os
import re
from datetime import datetime

from PyQt5.QtCore import Qt, QEvent, QTimer, QSize, QPoint, QRect, QRectF, QMimeData, pyqtSignal
from PyQt5.QtGui import (
    QTextCursor, QFont, QPixmap, QPainter, QColor, QPen, QKeySequence, QDrag,
)
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QTextEdit,
    QSizePolicy, QApplication, QLineEdit, QScrollArea, QFrame,
    QListWidget, QCheckBox, QShortcut, QDialog, QDialogButtonBox, QTabWidget,
)

from styles.style_all import (
    theme,
    tk,
    restyle_card_title,
    restyle_card_frame,
    apply_btn_download,
    apply_medium_button,
    make_glyph_icon,
    MEDIUM_BUTTON_H,
    CARD_LEFT_GAP,
    VIDEO_TAB_QSS,
    fmt,
)
from pages.seq_dup_panel import SeqDupPanel
from pages.editor_speak import EditorSpeak, make_speak_button, style_speak_button
from utils.flow_layout import FlowLayout, FlowLayoutHost
from utils.logger import get_logger

log = get_logger(__name__)
_KEEP_VER_FALLBACK = "v9.19"


# ── 前缀符号历史卡片：参照「提示词」分类卡 —— 彩色圆角卡 + 影子拖拽排序 ──
_PREFIX_MAX = 24          # 历史前缀上限
_PREFIX_CARD_H = 30       # 卡片高（压缩以适配小窗口）
_PREFIX_CARD_MIN_W = 46   # 卡片最小宽
_PREFIX_CARD_MAX_W = 180  # 较长符号在卡片内省略，完整内容仍可应用
_PREFIX_DRAG_PX = 6       # 拖拽启动阈值
_PREFIX_GUIDE_W = 12      # 拖拽插入虚线宽
_PREFIX_SPACING = 6
_NUMBER_STYLES = (
    ("decimal", "1.2.", "数字加句点"),
    ("upper", "A.B.", "大写字母"),
    ("circled", "① ②", "带圈数字"),
    ("chinese", "一、 二、", "中文序号"),
    ("comma", "1、2、", "数字加顿号"),
    ("brackets", "(1)(2)", "数字加括号"),
    ("right_bracket", "1)2)", "数字加右括号"),
    ("lower", "a.b.", "小写字母"),
)
_PREFIX_BLANK = re.compile(r"[\s\u00ad\u180e\u200b-\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff]*")
_NUMBER_PREFIX = re.compile(
    r"^(?:(?:\d+|[A-Za-z])[.．](?!\d)|(?:[A-Z]{2,5}|[a-z]{2,5})[.．](?=\s)"
    r"|(?:\d+|[A-Z]{1,5}|[a-z]{1,5})[、)）]"
    r"|[（(]\d+[）)]|[零〇一二三四五六七八九十百千万亿]+、|[①-⑳㉑-㉟㊱-㊿])\s*"
)
_PREFIX_PALETTE = (
    "#3b82f6", "#8b5cf6", "#ec4899", "#ef4444", "#f97316",
    "#eab308", "#22c55e", "#14b8a6", "#06b6d4", "#f43f5e",
    "#6366f1", "#a855f7",
)


def _mix_hex(a: str, b: str, t: float) -> str:
    ca, cb = QColor(a), QColor(b)
    return QColor(
        int(ca.red() + (cb.red() - ca.red()) * t),
        int(ca.green() + (cb.green() - ca.green()) * t),
        int(ca.blue() + (cb.blue() - ca.blue()) * t),
    ).name()


def _pfx_contrast_fg(bg: str) -> str:
    try:
        c = QColor(bg)
        lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
        return "#111111" if lum > 150 else "#ffffff"
    except Exception:
        return "#ffffff"


# 句末才断行。逗号、顿号不断。「.」要躲开小数、缩写、序号和网址。
_SENT_END = set("。！？!?")
_CLOSERS = set("\"'”’）)】》」』]}")
_ABBREV = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc",
    "ie", "eg", "am", "pm", "inc", "ltd", "fig", "vol", "pp", "ed", "al",
}
_NO_SPACE = set("，。！？、；：）】》」』”’\"'（(【《「『［[．…,.!?;:")
_LIST_START = re.compile(
    r"^(?:[-*•·]|\d{1,3}[.、．)]|[（(]\d{1,3}[）)]|[一二三四五六七八九十]{1,3}[、．.])"
)

# 单段源文上限。translate_job 一次最多生成 512 token，再长会被截断。
_TR_CHUNK = 180


def _chunk_body(text: str, limit: int = _TR_CHUNK) -> list:
    """长段按句末切开，切不开就按上限硬切。标点留在前一段。"""
    if len(text) <= limit:
        return [text] if text else []
    chunks = []
    i = 0
    n = len(text)
    while i < n:
        end = min(i + limit, n)
        cut = end
        if end < n:
            for j in range(end - 1, i + limit // 3, -1):
                ch = text[j]
                if ch in "。！？!?\n；;":
                    cut = j + 1
                    break
                if ch == "." and (j + 1 >= n or text[j + 1].isspace()):
                    cut = j + 1
                    break
        if cut <= i:
            cut = min(i + limit, n)
        chunks.append(text[i:cut])
        i = cut
    return chunks


def split_translate_pieces(text: str) -> list:
    """拆成 ('ws', 空白) 与 ('body', 正文)。空白原样拼回，不送给模型。"""
    pieces = []
    for part in re.split(r"(\s+)", text or ""):
        if not part:
            continue
        if part.isspace():
            pieces.append(("ws", part))
        else:
            pieces.extend(("body", chunk) for chunk in _chunk_body(part))
    return pieces


def join_translated_pieces(pieces: list, outs: list) -> tuple:
    """拼回全文。空译文保留该段原文。返回 (text, changed, failed)。"""
    it = iter(outs or [])
    built = []
    changed = 0
    failed = 0
    for kind, text in pieces:
        if kind == "ws":
            built.append(text)
            continue
        got = str(next(it, "") or "").strip()
        if not got:
            built.append(text)
            failed += 1
            continue
        built.append(got)
        if got != text:
            changed += 1
    return "".join(built), changed, failed


def _is_cjk_letter(ch: str) -> bool:
    o = ord(ch)
    return (
        0x4E00 <= o <= 0x9FFF
        or 0x3400 <= o <= 0x4DBF
        or 0xF900 <= o <= 0xFAFF
        or 0x3040 <= o <= 0x30FF
        or 0xAC00 <= o <= 0xD7AF
    )


def _ascii_word_before(text: str, i: int) -> str:
    j = i - 1
    while j >= 0 and text[j].isascii() and text[j].isalpha():
        j -= 1
    return text[j + 1:i]


def _is_list_marker_period(text: str, i: int) -> bool:
    """行首的 1. / 12． 是序号，不是句号。"""
    j = i - 1
    while j >= 0 and text[j].isdigit():
        j -= 1
    if j == i - 1:
        return False
    if i - j - 1 > 3:
        return False
    k = j
    while k >= 0 and (text[k].isspace() or text[k] in "(（"):
        k -= 1
    return k < 0


def _period_ends_sentence(text: str, i: int) -> bool:
    if _is_list_marker_period(text, i):
        return False
    prev = text[i - 1] if i else ""
    nxt = text[i + 1] if i + 1 < len(text) else ""
    if prev.isdigit() and nxt.isdigit():
        return False
    word = _ascii_word_before(text, i)
    if word and (len(word) == 1 or word.lower() in _ABBREV):
        return False
    if nxt and nxt.isascii() and nxt.isalnum():
        return False
    return True


def _sentence_end_span(text: str, i: int) -> int:
    """text[i] 若是句末，返回断行位置（含句末后的引号括号）；否则 0。"""
    n = len(text)
    ch = text[i]
    end = 0
    if ch in _SENT_END:
        j = i + 1
        while j < n and text[j] in _SENT_END:
            j += 1
        end = j
    elif ch == "…":
        j = i + 1
        while j < n and text[j] == "…":
            j += 1
        end = j
    elif ch == "．":
        if not _is_list_marker_period(text, i):
            end = i + 1
    elif ch == ".":
        if i + 2 < n and text[i:i + 3] == "...":
            j = i
            while j < n and text[j] == ".":
                j += 1
            if j - i >= 3:
                end = j
        elif _period_ends_sentence(text, i):
            end = i + 1
            k = end
            while k < n and text[k] in _CLOSERS:
                k += 1
            t = k
            while t < n and text[t] in " \t":
                t += 1
            if t < n and text[t].isascii() and text[t].islower():
                end = 0
    if end == 0:
        return 0
    while end < n and text[end] in _CLOSERS:
        end += 1
    return end


def _ends_with_sentence(text: str) -> bool:
    s = text.rstrip()
    if not s:
        return False
    i = len(s) - 1
    while i >= 0 and s[i] in _CLOSERS:
        i -= 1
    if i < 0:
        return False
    if s[i] in _SENT_END or s[i] == "…":
        return True
    if s[i] == "．":
        return not _is_list_marker_period(s, i)
    if s[i] == ".":
        j = i
        while j >= 0 and s[j] == ".":
            j -= 1
        if i - j >= 3:
            return True
        return _period_ends_sentence(s, i) and _sentence_end_span(s, i) == len(s)
    return False


def _glue(prev: str, nxt: str) -> str:
    a = prev[-1]
    b = nxt[0]
    if _is_cjk_letter(a) or _is_cjk_letter(b):
        return ""
    if a in _NO_SPACE or b in _NO_SPACE:
        return ""
    if a == "-" and b.isascii() and b.islower():
        return ""
    return " "


def _concat_wrapped(prev: str, nxt: str) -> str:
    left = prev.rstrip()
    right = nxt.lstrip()
    if not left:
        return right
    if not right:
        return left
    return left + _glue(left, right) + right


def _should_join_wrapped(prev: str, nxt: str) -> bool:
    """只接硬换行：逗号收尾、英文词被拆开、或较长的中文行。短行和清单留下。"""
    if _ends_with_sentence(prev) or _LIST_START.match(nxt):
        return False
    p = prev.rstrip()
    n = nxt.lstrip()
    if not p or not n:
        return False
    if n[0] in "，、。！？!?：:；;,.)）】》」』”’\"'":
        return True
    if p[-1] in "，、：:、,（(““‘「『【《":
        return True
    if p[-1] == "-" and n[0].isascii() and n[0].islower():
        return True
    if p[-1].isascii() and p[-1].isalpha() and n[0].isascii() and n[0].islower():
        return True
    if _is_cjk_letter(p[-1]) and len(p) >= 16:
        return True
    return False


def _split_sentences(text: str) -> list:
    out = []
    buf = []
    i = 0
    n = len(text)
    while i < n:
        span = _sentence_end_span(text, i)
        buf.append(text[i])
        if span:
            i += 1
            while i < span:
                buf.append(text[i])
                i += 1
            piece = "".join(buf).strip()
            buf = []
            if piece:
                out.append(piece)
            while i < n and text[i] in " \t":
                i += 1
            continue
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def _join_wrapped_lines(lines: list) -> str:
    kept = [ln.strip() for ln in lines if ln.strip()]
    if not kept:
        return ""
    merged = [kept[0]]
    for ln in kept[1:]:
        if _should_join_wrapped(merged[-1], ln):
            merged[-1] = _concat_wrapped(merged[-1], ln)
        else:
            merged.append(ln)
    sentences = []
    for piece in merged:
        sentences.extend(_split_sentences(piece))
    return "\n".join(sentences)


def smart_line_breaks(raw: str) -> str:
    """空行仍隔开段落。段落里的硬换行先接上，再按句末拆成一行一句。"""
    if not raw or not str(raw).strip():
        return ""
    norm = (
        str(raw).replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\u2028", "\n")
        .replace("\u2029", "\n")
    )
    paras = []
    for block in re.split(r"\n[ \t]*\n", norm):
        joined = _join_wrapped_lines(block.split("\n"))
        if joined:
            paras.append(joined)
    return "\n\n".join(paras)


def remove_right_margin_breaks(text: str) -> str:
    """接回句内硬换行，保留空行、句末断行和明显的段落起点。"""
    lines = text.split("\n")
    parts = [lines[0]]
    for previous, line in zip(lines, lines[1:]):
        left, right = previous.rstrip(), line.lstrip()
        if (
            not left or not right
            or _ends_with_sentence(left)
            or line.startswith(("  ", "\t", "\u3000"))
            or _LIST_START.match(right)
        ):
            parts.extend(("\n", line))
            continue

        left_char, right_char = left[-1], right[0]
        parts[-1] = parts[-1].rstrip()
        if not (
            _is_cjk_letter(left_char) or _is_cjk_letter(right_char)
            or left_char in "([{/（【《「『“‘"
            or right_char in ",.!?;:%)]}，。！？、；：）】》」』”’…"
            or (left_char == "-" and right_char.isascii() and right_char.islower())
        ):
            parts.append(" ")
        parts.append(right)
    return "".join(parts)


def remove_intrasentence_whitespace(text: str) -> str:
    """清理句内空白，保留句末分隔、空行和段首缩进。"""
    def replace_gap(match: re.Match) -> str:
        gap = match.group()
        start, end = match.span()
        if start == 0 or end == len(text):
            return gap

        lines = gap.split("\n")
        if len(lines) > 2 or (len(lines) == 2 and lines[-1].startswith(("  ", "\t", "\u3000"))):
            return gap

        mark = start - 1
        while mark >= 0 and text[mark] in _CLOSERS:
            mark -= 1
        if mark < 0 or text[end] in "”’）)】》」』]}":
            return ""

        punctuation = text[mark]
        if punctuation in _SENT_END or punctuation == "．":
            return gap
        # 省略号可以表示句中的停顿。普通句点仍保留后面的句间空白。
        if punctuation == "." and text[mark - 1:mark] != "." and _period_ends_sentence(text, mark):
            return gap
        return ""

    return re.sub(r"\s+", replace_gap, text)


_LAYOUT_MARKER = re.compile(r"^(?:#{1,6}(?:\s|$)|[>|])")
_LAYOUT_LIST = re.compile(r"^(?:\+\s|[A-Za-z][.)]\s)")
_LAYOUT_FENCE = re.compile(r"^(?:`{3,}|~{3,})")


def format_paragraphs(text: str, *, blank_line: bool, indent: bool, trim_end: bool, line_range=None) -> str:
    """以编辑器里实际的换行为段落边界。标题、列表、引用、表格和围栏代码保留结构。"""
    result = []
    blanks = []
    previous_paragraph = False
    in_list = False
    fence = ""
    for index, line in enumerate(text.split("\n")):
        selected = line_range is None or line_range[0] <= index <= line_range[1]
        content = line.lstrip()
        marker = _LAYOUT_FENCE.match(content)
        if fence or marker:
            result.extend(blanks)
            blanks.clear()
            result.append(line)
            previous_paragraph = False
            in_list = False
            if marker:
                token = marker.group()
                if not fence:
                    fence = token
                elif token.startswith(fence) and not content[marker.end():].strip():
                    fence = ""
            continue

        if trim_end and selected:
            line = line.rstrip()
            content = content.rstrip()
        if not content:
            blanks.append(line)
            continue

        list_continuation = in_list and not blanks and line.startswith(("  ", "\t"))
        in_list = bool(list_continuation or _LIST_START.match(content) or _LAYOUT_LIST.match(content))
        paragraph = selected and not (in_list or _LAYOUT_MARKER.match(content))
        if blank_line and previous_paragraph and paragraph:
            result.append("")
        else:
            result.extend(blanks)
        blanks.clear()
        result.append("\u3000\u3000" + content if indent and paragraph else line)
        previous_paragraph = paragraph

    result.extend(blanks)
    return "\n".join(result)


def _selected_text_offsets(cursor):
    """把 Qt 的 UTF-16 光标位置换算为 Python 文本下标。"""
    prefix = QTextCursor(cursor)
    prefix.setPosition(cursor.selectionStart())
    prefix.movePosition(QTextCursor.Start, QTextCursor.KeepAnchor)
    start = len(prefix.selectedText())
    return start, start + len(cursor.selectedText())


class _PrefixCard(QWidget):
    """前缀卡片：彩色圆角（仿提示词分类卡）。单击回填 / ✕删除 / 按住拖动排序。

    拖拽方式与提示词页一致：不弹 QDrag，原地保留 + 半透明影子跟手 +
    卡片间虚线插入线，松手落位后重排。
    """

    clicked = pyqtSignal(str)
    remove_requested = pyqtSignal(str)

    def __init__(self, symbol: str, accent: str, owner, parent=None):
        super().__init__(parent)
        self.symbol = symbol
        self._owner = owner
        self._press = None
        self._dragging = False
        self.setObjectName("VoicePrefixCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAutoFillBackground(False)
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip(f"点击添加行首前缀「{symbol}」，拖动可排序")
        self.setFixedHeight(_PREFIX_CARD_H)
        self.setMinimumWidth(_PREFIX_CARD_MIN_W)

        self._accent = accent
        self._selected = False
        self._apply_card_style()

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 20, 0)
        lay.setSpacing(0)
        lab = QLabel(symbol)
        lab.setTextFormat(Qt.PlainText)
        lab.setAlignment(Qt.AlignCenter)
        lab.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lab.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(lab, 1)
        self._lab = lab

        self.btn_x = QPushButton("✕", self)
        self.btn_x.setObjectName("VoicePrefixCardDel")
        self.btn_x.setCursor(Qt.PointingHandCursor)
        self.btn_x.setFocusPolicy(Qt.NoFocus)
        self.btn_x.setFixedSize(18, 18)
        self.btn_x.clicked.connect(lambda: self.remove_requested.emit(self.symbol))
        self._place_x()

    def _place_x(self):
        b = self.btn_x
        b.setGeometry(self.width() - b.width() - 1, 1, b.width(), b.height())
        b.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_x()
        margins = self.layout().contentsMargins()
        width = max(0, self.width() - margins.left() - margins.right())
        self._lab.setText(self._lab.fontMetrics().elidedText(self.symbol, Qt.ElideRight, width))

    def apply_theme(self):
        self._apply_card_style()

    def set_selected(self, selected: bool):
        selected = bool(selected)
        if selected == self._selected:
            return
        self._selected = selected
        self._apply_card_style()

    def _apply_card_style(self):
        accent = self._accent
        fg = _pfx_contrast_fg(accent)
        # 选中/未选中都用 2px，避免白框出现时卡片内缩一圈
        bd = "#ffffff" if self._selected else _mix_hex(accent, "#ffffff", 0.10)
        self.setStyleSheet(
            "#VoicePrefixCard{background-color:%s;border:2px solid %s;"
            "border-radius:8px;}"
            "#VoicePrefixCard QLabel{background:transparent;border:none;color:%s;"
            "font-size:12px;font-weight:600;}"
            "QPushButton#VoicePrefixCardDel{background:transparent;border:none;"
            "color:rgba(255,255,255,200);font-size:12px;font-weight:700;padding:0;}"
            "QPushButton#VoicePrefixCardDel:hover{color:#111111;}"
            % (accent, bd, fg)
        )

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.globalPos()
            self._dragging = False
        e.accept()

    def mouseMoveEvent(self, e):
        if self._press is None or not (e.buttons() & Qt.LeftButton):
            return
        if not self._dragging:
            if (e.globalPos() - self._press).manhattanLength() < _PREFIX_DRAG_PX:
                return
            self._dragging = True
            self.setCursor(Qt.ClosedHandCursor)
            self._owner._begin_prefix_drag(self)
        self._owner._move_prefix_drag(e.globalPos())

    def mouseReleaseEvent(self, e):
        was_drag = self._dragging
        if was_drag:
            self._owner._end_prefix_drag(e.globalPos())
        self._dragging = False
        self._press = None
        self.setCursor(Qt.OpenHandCursor)
        if e.button() == Qt.LeftButton and not was_drag:
            if self.rect().contains(e.pos()):
                self.clicked.emit(self.symbol)
        super().mouseReleaseEvent(e)


class _PrefixDragPreview(QWidget):
    """拖拽半透明影子：跟鼠标走，原卡不动。"""

    def __init__(self, pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self.setObjectName("VoicePrefixDragPreview")
        self._pm = QPixmap(pixmap)
        self.setFixedSize(max(1, self._pm.width()), max(1, self._pm.height()))
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.hide()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setOpacity(0.55)
        p.drawPixmap(0, 0, self._pm)
        p.end()


class _PrefixDropGuide(QWidget):
    """拖拽插入虚线：竖在两张卡之间或最前/最后。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("VoicePrefixDropGuide")
        self.setFixedSize(_PREFIX_GUIDE_W, _PREFIX_CARD_H)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.hide()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        try:
            dark = bool(theme.is_dark)
        except Exception:
            dark = True
        color = QColor("#ffffff" if dark else "#334155")
        pen = QPen(color, 2, Qt.CustomDashLine)
        pen.setDashPattern([3, 3])
        pen.setCapStyle(Qt.FlatCap)
        p.setPen(pen)
        x = self.width() // 2
        p.drawLine(x, 3, x, self.height() - 3)
        p.end()


_CHAR_CELL_H = 22
_CHAR_GAP = 3
_SYMBOL_INPUT_W = 180


class _CharPreview(QWidget):
    """输入框下方：每个字符一块小格子，空格显示成可见空位。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SymbolCharPreview")
        self.setAccessibleName("字符预览")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedSize(_SYMBOL_INPUT_W, 28)
        f = QFont(self.font())
        f.setPixelSize(12)
        self.setFont(f)
        self._text = ""

    def set_text(self, text: str):
        text = text if isinstance(text, str) else str(text or "")
        if text == self._text:
            return
        self._text = text
        self.update()

    def _cells(self):
        fm = self.fontMetrics()
        out = []
        for ch in self._text:
            space = ch in (" ", "\t", "\u00a0", "\u3000")
            if ch == "\t":
                shown = "⇥"
            elif space:
                shown = "␣"
            else:
                shown = ch
            w = max(18, int(fm.horizontalAdvance(shown)) + 10)
            if ch == "\t":
                w = max(w, 28)
            elif ch == "\u3000":
                w = max(w, 24)
            out.append((shown, space, w))
        return out

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        if not self._text:
            p.setPen(QColor(tk("text_dim")))
            p.drawText(self.rect().adjusted(2, 0, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, "字符预览")
            p.end()
            return
        try:
            fg = QColor(tk("text"))
            dim = QColor(tk("text_dim"))
            bd = QColor(tk("border"))
            bg = QColor(tk("input_bg"))
        except Exception:
            fg = QColor("#e2e8f0")
            dim = QColor("#94a3b8")
            bd = QColor("#475569")
            bg = QColor("#1e293b")
        y = (self.height() - _CHAR_CELL_H) / 2.0
        x = 0.0
        limit = float(self.width())
        for shown, space, w in self._cells():
            if x >= limit:
                break
            r = QRectF(x + 0.5, y + 0.5, min(w - 1.0, limit - x - 0.5), _CHAR_CELL_H - 1.0)
            if space:
                p.setBrush(Qt.NoBrush)
                pen = QPen(dim, 1, Qt.DashLine)
                p.setPen(pen)
                p.drawRoundedRect(r, 4, 4)
                p.setPen(dim)
            else:
                p.setBrush(bg)
                p.setPen(QPen(bd, 1))
                p.drawRoundedRect(r, 4, 4)
                p.setPen(fg)
            p.drawText(r, Qt.AlignCenter, shown)
            x += w + _CHAR_GAP
        p.end()


_SEP_PX = 3
_TEXT_ACTION_SPACING = 4
_TEXT_TOOL_MIME = "application/x-desktop-assistant-text-tool"
_UNDO_MAX = 5           # 操作记录「撤销 / 重做」最多 5 步
_UNDO_IDLE_MS = 700     # 连续输入停这么久算一步


class _TextToolButton(QPushButton):
    def __init__(self, text):
        super().__init__(text)
        self._press_pos = None

    def mousePressEvent(self, event):
        self._press_pos = event.pos() if event.button() == Qt.LeftButton else None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        press = self._press_pos
        if (
            press is None or not (event.buttons() & Qt.LeftButton)
            or (event.pos() - press).manhattanLength() < QApplication.startDragDistance()
        ):
            super().mouseMoveEvent(event)
            return

        self._press_pos = None
        self.setDown(False)
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(_TEXT_TOOL_MIME, b"1")
        drag.setMimeData(mime)
        preview = self.grab()
        painter = QPainter(preview)
        painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 170))
        painter.end()
        drag.setPixmap(preview)
        drag.setHotSpot(press)
        try:
            drag.exec_(Qt.MoveAction)
        finally:
            self.parentWidget().clear_drop_guide()

    def mouseReleaseEvent(self, event):
        self._press_pos = None
        super().mouseReleaseEvent(event)


class _TextToolGrid(QWidget):
    order_changed = pyqtSignal()
    COLUMNS = 3

    def __init__(self, buttons, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._buttons = buttons
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(_TEXT_ACTION_SPACING)
        for column in range(self.COLUMNS):
            self._grid.setColumnStretch(column, 1)
        self._guide = QFrame(self)
        self._guide.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._guide.hide()
        self.restore_order(None)

    def order(self):
        return list(self._order)

    def restore_order(self, saved):
        keys = saved if isinstance(saved, list) else []
        order = dict.fromkeys(key for key in keys if isinstance(key, str) and key in self._buttons)
        order.update(dict.fromkeys(self._buttons))
        self._arrange(list(order))

    def _arrange(self, order):
        self._order = order
        for button in self._buttons.values():
            self._grid.removeWidget(button)
        for index, key in enumerate(order):
            row, column = divmod(index, self.COLUMNS)
            self._grid.addWidget(self._buttons[key], row, column)

    def clear_drop_guide(self):
        self._guide.hide()

    def _drag_source(self, event):
        if not event.mimeData().hasFormat(_TEXT_TOOL_MIME):
            return None
        source = event.source()
        return next((i for i, key in enumerate(self._order) if self._buttons[key] is source), None)

    def _drop_location(self, pos):
        rects = [self._buttons[key].geometry() for key in self._order]
        row_starts = range(0, len(rects), self.COLUMNS)
        row = min(row_starts, key=lambda index: abs(rects[index].center().y() - pos.y()))
        row_end = min(row + self.COLUMNS, len(rects))
        for index in range(row, row_end):
            rect = rects[index]
            if pos.x() <= rect.center().x():
                return index, self._guide_rect(rect, after=False)
        return row_end, self._guide_rect(rects[row_end - 1], after=True)

    def _guide_rect(self, target, after):
        gap = _TEXT_ACTION_SPACING // 2
        x = target.right() + gap if after else target.left() - gap
        return QRect(max(0, min(x, self.width() - 2)), target.y(), 2, target.height())

    def dragEnterEvent(self, event):
        if self._drag_source(event) is None:
            event.ignore()
            return
        self._guide.setStyleSheet(f"background:{tk('text')};border:none;")
        event.acceptProposedAction()

    def dragMoveEvent(self, event):
        source = self._drag_source(event)
        if source is None:
            event.ignore()
            return
        insert, rect = self._drop_location(event.pos())
        if insert in (source, source + 1):
            self.clear_drop_guide()
        else:
            self._guide.setGeometry(rect)
            self._guide.show()
            self._guide.raise_()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.clear_drop_guide()
        event.accept()

    def dropEvent(self, event):
        self.clear_drop_guide()
        source = self._drag_source(event)
        if source is None:
            event.ignore()
            return
        insert, _ = self._drop_location(event.pos())
        if insert not in (source, source + 1):
            order = self.order()
            key = order.pop(source)
            order.insert(insert - int(source < insert), key)
            self._arrange(order)
            self.order_changed.emit()
        event.acceptProposedAction()


class PageTextEditor(QWidget):
    """文本编辑：左编辑区；右栏三区「操作记录 / 文本操作 / 文件处理」。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PageRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._theme_titles = []
        self._theme_frames = []
        self._func_cards = []
        self._h_seps = []
        self._symbol_history = []      # 用过的前缀符号（顺序即卡片顺序）
        self._selected_symbol = None   # 当前选中的前缀卡（白框）；点空白取消
        self._prefs_dirty = None       # 主窗口注入：有变动就安排落盘 user.txt
        self._click_filter_on = False
        self._undo_stack = []
        self._redo_stack = []
        self._last_snapshot = ""
        self._suspend_history = False
        self._undo_timer = QTimer(self)
        self._undo_timer.setSingleShot(True)
        self._undo_timer.setInterval(_UNDO_IDLE_MS)
        self._undo_timer.timeout.connect(self._commit_typed_undo)
        self._tr_worker = None
        self._tr_busy = False
        self._tr_req = 0

        self._build_ui()
        self._speak = EditorSpeak(self)
        self._speak.changed.connect(self._paint_speak_btns)
        self._speak.failed.connect(self._on_speak_failed)
        self._speak_tip = "有选区读选区，否则从光标读到文末"
        self._speak_tip_timer = QTimer(self)
        self._speak_tip_timer.setSingleShot(True)
        self._speak_tip_timer.setInterval(3000)
        self._speak_tip_timer.timeout.connect(self._restore_speak_tips)
        self._esc_speak = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self._esc_speak.setContext(Qt.WidgetWithChildrenShortcut)
        self._esc_speak.activated.connect(self._stop_speak)
        self._esc_speak.setEnabled(False)
        self._paint_speak_btns()
        theme.changed.connect(self.refresh_theme)
        self._install_blank_click_filter()

    def minimumSizeHint(self):
        return QSize(0, 0)

    def sizeHint(self):
        return QSize(900, 600)

    def _section_title(self, text: str) -> QLabel:
        """栏内标题：只刷字色，不 restyle 容器（避免内层再套一张功能卡）。"""
        lbl = QLabel(text)
        lbl.setProperty("role", "card-title")
        restyle_card_title(lbl)
        self._theme_titles.append(lbl)
        return lbl

    def _make_hsep(self) -> QFrame:
        f = QFrame()
        f.setObjectName("TextEditorHSep")
        f.setFrameShape(QFrame.NoFrame)
        f.setFixedHeight(_SEP_PX)
        f.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        f.setAttribute(Qt.WA_StyledBackground, True)
        self._h_seps.append(f)
        return f

    # ── 构建 ──────────────────────────────────────────────────────────
    def _build_ui(self):
        # ContentRoot 已有左右 14px。再写 CARD_LEFT_GAP 会叠成 26px。
        # 与积分计算 / 系统总览相同：页面 0 边距，不再套第二层。
        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        page.addLayout(body, 1)

        # ══ 左 70%：编辑区 + 翻译 / 速存文本 / 复制 / 清空 ══════════════
        left = QWidget()
        left.setObjectName("TextEditorLeft")
        left.setAttribute(Qt.WA_StyledBackground, True)
        left.setStyleSheet("#TextEditorLeft{background:transparent;border:none;}")
        left.setMinimumWidth(0)
        left.setMinimumHeight(0)
        left.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        cve = QVBoxLayout(left)
        cve.setContentsMargins(0, 0, 0, 0)
        cve.setSpacing(4)

        row_title = QHBoxLayout()
        row_title.setContentsMargins(0, 0, 0, 0)
        row_title.setSpacing(8)
        row_title.addWidget(self._section_title("编辑区"), 0, Qt.AlignVCenter)
        row_title.addStretch(1)
        speak_row = QHBoxLayout()
        speak_row.setContentsMargins(0, 0, 0, 0)
        speak_row.setSpacing(4)
        self.btn_speak_m = make_speak_button("男")
        self.btn_speak_f = make_speak_button("女")
        self.btn_speak_m.clicked.connect(lambda: self._on_speak_btn("男"))
        self.btn_speak_f.clicked.connect(lambda: self._on_speak_btn("女"))
        speak_row.addWidget(self.btn_speak_m, 0, Qt.AlignVCenter)
        speak_row.addWidget(self.btn_speak_f, 0, Qt.AlignVCenter)
        row_title.addLayout(speak_row, 0)
        self.chk_word_wrap = QCheckBox("自动换行")
        self.chk_word_wrap.setChecked(True)
        self.chk_word_wrap.setStyleSheet("background:transparent;")
        self.chk_word_wrap.setMinimumWidth(0)
        self.chk_word_wrap.setCursor(Qt.PointingHandCursor)
        self.chk_word_wrap.toggled.connect(self._on_word_wrap_toggled)
        row_title.addWidget(self.chk_word_wrap, 0, Qt.AlignVCenter)
        cve.addLayout(row_title)

        self.editor = QTextEdit()
        self.editor.setObjectName("PasteContentEdit")
        self.editor.setAcceptRichText(False)
        self.editor.setLineWrapMode(QTextEdit.WidgetWidth)
        self.editor.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.editor.setMinimumHeight(0)
        self.editor.setMinimumWidth(0)
        self.editor.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        try:
            self.editor.document().setDocumentMargin(0)
        except Exception:
            pass
        self.editor.setViewportMargins(0, 0, 0, 0)
        self.editor.setUndoRedoEnabled(False)
        self.editor.textChanged.connect(self._on_editor_text_changed)
        cve.addWidget(self.editor, 1)

        row_edit_btn = QHBoxLayout()
        row_edit_btn.setSpacing(_TEXT_ACTION_SPACING)
        self.chk_tr_selection = QCheckBox("选中文本")
        self.chk_tr_selection.setChecked(False)
        self.chk_tr_selection.setStyleSheet("background:transparent;")
        self.chk_tr_selection.setMinimumWidth(0)
        self.chk_tr_selection.setCursor(Qt.PointingHandCursor)
        self.btn_tr_en = apply_medium_button(QPushButton("En"))
        self.btn_tr_en.setToolTip("译为英文")
        self.btn_tr_en.setMinimumHeight(28)
        self.btn_tr_en.setMinimumWidth(0)
        self.btn_tr_en.clicked.connect(lambda: self._translate_editor("zh-en"))
        self.btn_tr_zh = apply_medium_button(QPushButton("Ch"))
        self.btn_tr_zh.setToolTip("译为中文")
        self.btn_tr_zh.setMinimumHeight(28)
        self.btn_tr_zh.setMinimumWidth(0)
        self.btn_tr_zh.clicked.connect(lambda: self._translate_editor("en-zh"))
        self.btn_quick_keep = apply_medium_button(QPushButton("速存"))
        self.btn_quick_keep.setMinimumHeight(28)
        self.btn_quick_keep.setMinimumWidth(0)
        self.btn_quick_keep.clicked.connect(self._quick_keep)
        self.btn_copy = QPushButton("复制")
        apply_btn_download(self.btn_copy)
        self.btn_copy.setMinimumHeight(28)
        self.btn_copy.setMinimumWidth(0)
        self.btn_copy.setCursor(Qt.PointingHandCursor)
        self.btn_copy.clicked.connect(self._copy_text)
        self.btn_clear_editor = apply_medium_button(QPushButton("清空"))
        self.btn_clear_editor.setMinimumHeight(28)
        self.btn_clear_editor.setMinimumWidth(0)
        self.btn_clear_editor.clicked.connect(self._on_clear_editor)
        self.btn_undo = apply_medium_button(QPushButton())
        self.btn_redo = apply_medium_button(QPushButton())
        for button, label, action in (
            (self.btn_undo, "撤销", self._undo_editor),
            (self.btn_redo, "重做", self._redo_editor),
        ):
            button.setFixedSize(MEDIUM_BUTTON_H, MEDIUM_BUTTON_H)
            button.setStyleSheet(f"padding: 0; min-width: {MEDIUM_BUTTON_H - 2}px;")
            button.setIconSize(QSize(22, 22))
            button.setAccessibleName(label)
            button.setToolTip(label)
            button.clicked.connect(action)
        self._refresh_history_icons()
        row_edit_btn.addWidget(self.chk_tr_selection, 0, Qt.AlignVCenter)
        row_edit_btn.addWidget(self.btn_tr_en, 0)
        row_edit_btn.addWidget(self.btn_tr_zh, 0)
        row_edit_btn.addStretch(1)
        row_edit_btn.addWidget(self.btn_quick_keep, 0)
        row_edit_btn.addWidget(self.btn_copy, 0)
        row_edit_btn.addWidget(self.btn_clear_editor, 0)
        row_edit_btn.addWidget(self.btn_undo, 0)
        row_edit_btn.addWidget(self.btn_redo, 0)
        cve.addLayout(row_edit_btn)


        # ══ 右 30%：操作记录和文本操作页签 ════════════════════════
        right = QWidget()
        right.setObjectName("TextEditorRight")
        right.setAttribute(Qt.WA_StyledBackground, True)
        right.setStyleSheet("#TextEditorRight{background:transparent;border:none;}")
        right.setMinimumHeight(0)
        right.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(4)

        # —— 1) 操作记录 ——
        card_log = QWidget()
        card_log.setObjectName("CardTextEditorLog")
        card_log.setAttribute(Qt.WA_StyledBackground, True)
        card_log.setStyleSheet("#CardTextEditorLog{background:transparent;border:none;}")
        card_log.setMinimumHeight(0)
        card_log.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        cv3 = QVBoxLayout(card_log)
        cv3.setContentsMargins(0, 0, 0, 0)
        cv3.setSpacing(2)
        cv3.addWidget(self._section_title("操作记录"), 0, Qt.AlignLeft)
        self.log = QListWidget()
        self.log.setObjectName("SimpleRecordList")
        self.log.setMinimumWidth(0)
        self.log.setMinimumHeight(0)
        self.log.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.log.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.log.setWordWrap(True)
        self.log.setTextElideMode(Qt.ElideRight)
        cv3.addWidget(self.log, 1)
        rv.addWidget(card_log, 28)
        self._refresh_undo_btns()

        self.tool_tabs = QTabWidget()
        self.tool_tabs.setObjectName("VideoTabWidget")
        self.tool_tabs.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        tab_bar = self.tool_tabs.tabBar()
        tab_bar.setDrawBase(False)
        tab_bar.setExpanding(False)
        tab_bar.setElideMode(Qt.ElideNone)
        rv.addWidget(self.tool_tabs, 72)

        # —— 2) 文本编辑（整理、替换、加序号、加符号）——
        edit_panel = QWidget()
        edit_panel.setObjectName("TextEditorEditZone")
        edit_panel.setAttribute(Qt.WA_StyledBackground, True)
        edit_panel.setStyleSheet("#TextEditorEditZone{background:transparent;border:none;}")
        edit_panel.setMinimumHeight(0)
        edit_panel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        cve_r = QVBoxLayout(edit_panel)
        cve_r.setContentsMargins(0, 0, 0, 0)
        cve_r.setSpacing(4)
        cve_r.setAlignment(Qt.AlignTop)

        self.btn_right_margin = apply_medium_button(_TextToolButton("去右边界"))
        self.btn_right_margin.setToolTip("接回句子中间的硬换行，保留空行和明显的段落边界")
        self.btn_right_margin.clicked.connect(self._remove_right_margin)
        self.btn_one_para = apply_medium_button(_TextToolButton("整理成1段"))
        self.btn_one_para.clicked.connect(self._collapse_to_one_paragraph)
        self.btn_del_blank = apply_medium_button(_TextToolButton("删除空行"))
        self.btn_del_blank.clicked.connect(self._remove_blank_lines)
        self.btn_smart_wrap = apply_medium_button(_TextToolButton("智能换行"))
        self.btn_smart_wrap.clicked.connect(self._smart_wrap_lines)
        self.btn_remove_whitespace = apply_medium_button(_TextToolButton("去除空格"))
        self.btn_remove_whitespace.setToolTip("去除句内的空格和换行，保留句间分隔。选中文本时只处理选区")
        self.btn_remove_whitespace.clicked.connect(self._remove_whitespace)
        self.btn_smart_layout = apply_medium_button(_TextToolButton("智能排版"))
        self.btn_smart_layout.setToolTip("设置段间空行、段首缩进和行尾空白")
        self.btn_smart_layout.clicked.connect(self._smart_layout)
        text_tools = {
            "right_margin": self.btn_right_margin,
            "one_paragraph": self.btn_one_para,
            "delete_blank_lines": self.btn_del_blank,
            "smart_wrap": self.btn_smart_wrap,
            "remove_whitespace": self.btn_remove_whitespace,
            "smart_layout": self.btn_smart_layout,
        }
        for button in text_tools.values():
            button.setMinimumHeight(MEDIUM_BUTTON_H)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.setStyleSheet("padding: 0 4px;")
            button.setCursor(Qt.OpenHandCursor)
        self.tool_grid = _TextToolGrid(text_tools)
        self.tool_grid.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.tool_grid.order_changed.connect(self._mark_prefs_dirty)
        cve_r.addWidget(self.tool_grid)
        right.setMinimumWidth(self.tool_grid.sizeHint().width())
        cve_r.addWidget(self._make_hsep())

        row_replace = QHBoxLayout()
        row_replace.setSpacing(4)
        self.inp_find_text = QLineEdit()
        self.inp_replace_text = QLineEdit()
        for field, label in (
            (self.inp_find_text, "查找内容"),
            (self.inp_replace_text, "替换为"),
        ):
            field.setObjectName("CalcInput")
            field.setPlaceholderText(label)
            field.setAccessibleName(label)
            field.setFixedHeight(MEDIUM_BUTTON_H)
            field.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            field.setStyleSheet("padding: 4px 6px;")
            row_replace.addWidget(field, 1)
        self.inp_replace_text.setToolTip("留空可删除匹配的文字")
        self.btn_replace_text = apply_medium_button(QPushButton("替换"))
        self.btn_replace_text.setMinimumHeight(MEDIUM_BUTTON_H)
        self.btn_replace_text.setCursor(Qt.PointingHandCursor)
        self.btn_replace_text.setToolTip("替换选区内的所有匹配，未选中文字时替换全文")
        self.btn_replace_text.setEnabled(False)
        self.inp_find_text.textChanged.connect(
            lambda value: self.btn_replace_text.setEnabled(bool(value))
        )
        self.inp_find_text.returnPressed.connect(self.inp_replace_text.setFocus)
        self.inp_replace_text.returnPressed.connect(self.btn_replace_text.click)
        self.btn_replace_text.clicked.connect(self._replace_text)
        row_replace.addWidget(self.btn_replace_text)
        cve_r.addLayout(row_replace)
        cve_r.addWidget(self._make_hsep())

        prefix_header = QHBoxLayout()
        prefix_header.setSpacing(4)
        prefix_title = QLabel("行首前缀")
        prefix_title.setProperty("typo", "muted")
        prefix_header.addWidget(prefix_title)
        prefix_header.addStretch(1)
        self.chk_prefix_skip_blank = QCheckBox("忽略空行")
        self.chk_prefix_skip_blank.setStyleSheet("background:transparent;")
        self.chk_prefix_skip_blank.setChecked(True)
        self.chk_prefix_skip_blank.setToolTip("保留空行原样，序号只计算有内容的行")
        self.chk_prefix_skip_blank.toggled.connect(self._mark_prefs_dirty)
        prefix_header.addWidget(self.chk_prefix_skip_blank)
        self.inp_num_start = QLineEdit("1")
        self.inp_num_start.setObjectName("CalcInput")
        self.inp_num_start.setToolTip("起始序号（正整数；无效时按 1）")
        self.inp_num_start.setAccessibleName("起始序号")
        self.inp_num_start.setFixedHeight(MEDIUM_BUTTON_H)
        self.inp_num_start.setFixedWidth(46)
        self.inp_num_start.setStyleSheet("padding: 0 4px;")
        self.inp_num_start.setAlignment(Qt.AlignCenter)
        self.inp_num_start.setMaxLength(6)
        start_label = QLabel("起始")
        start_label.setProperty("typo", "muted")
        start_label.setBuddy(self.inp_num_start)
        prefix_header.addWidget(start_label)
        prefix_header.addWidget(self.inp_num_start)
        cve_r.addLayout(prefix_header)

        number_grid = QGridLayout()
        number_grid.setSpacing(4)
        for column in range(3):
            number_grid.setColumnStretch(column, 1)
        for index, (style, caption, tooltip) in enumerate(_NUMBER_STYLES):
            button = apply_medium_button(QPushButton(caption))
            button.setFixedHeight(MEDIUM_BUTTON_H)
            button.setStyleSheet("padding: 0 3px;")
            button.setToolTip(tooltip)
            button.setAccessibleName(tooltip)
            button.clicked.connect(lambda _checked=False, scheme=style: self._add_line_numbers(scheme))
            row, column = divmod(index, 3)
            number_grid.addWidget(button, row, column)
        cve_r.addLayout(number_grid)

        row_add_sym = QHBoxLayout()
        row_add_sym.setSpacing(4)
        self.btn_add_symbol = apply_medium_button(QPushButton("加符号"))
        self.btn_add_symbol.setMinimumHeight(MEDIUM_BUTTON_H)
        self.btn_add_symbol.setMinimumWidth(0)
        self.btn_add_symbol.setCursor(Qt.PointingHandCursor)
        self.btn_add_symbol.setToolTip("添加选中的符号卡")
        self.btn_add_symbol.setEnabled(False)
        self.btn_add_symbol.clicked.connect(self._apply_selected_symbol)
        row_add_sym.addWidget(self.btn_add_symbol)
        self.btn_symbol_save = apply_medium_button(QPushButton("+"))
        self.btn_symbol_save.setFixedWidth(36)
        self.btn_symbol_save.setMinimumHeight(MEDIUM_BUTTON_H)
        self.btn_symbol_save.setCursor(Qt.PointingHandCursor)
        self.btn_symbol_save.setToolTip("把当前前缀内容直接存成一张「前缀卡」")
        self.btn_symbol_save.setAccessibleName("保存为前缀卡")
        self.btn_symbol_save.clicked.connect(self._save_current_symbol_as_card)
        row_add_sym.addWidget(self.btn_symbol_save)
        row_add_sym.addStretch()
        cve_r.addLayout(row_add_sym)

        self.inp_symbol = QLineEdit()
        self.inp_symbol.setObjectName("CalcInput")
        self.inp_symbol.setPlaceholderText("自定义符号")
        self.inp_symbol.setAccessibleName("自定义行首符号")
        self.inp_symbol.setFixedHeight(MEDIUM_BUTTON_H)
        self.inp_symbol.setFixedWidth(_SYMBOL_INPUT_W)
        self.inp_symbol.setClearButtonEnabled(True)
        self.inp_symbol.setCursor(Qt.IBeamCursor)
        self.inp_symbol.returnPressed.connect(self.btn_add_symbol.click)
        self.inp_symbol.textChanged.connect(self._on_symbol_input_changed)
        cve_r.addWidget(self.inp_symbol, 0, Qt.AlignLeft)
        self.symbol_preview = _CharPreview()
        cve_r.addWidget(self.symbol_preview, 0, Qt.AlignLeft)
        self.inp_symbol.textChanged.connect(self.symbol_preview.set_text)

        self.symbol_row = QWidget()
        self.symbol_row.setObjectName("VoicePrefixRow")
        self.symbol_row.setAttribute(Qt.WA_StyledBackground, True)
        self.symbol_row.setStyleSheet("#VoicePrefixRow{background:transparent;border:none;}")
        self.symbol_row.setMinimumWidth(0)
        sr = QHBoxLayout(self.symbol_row)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.setSpacing(4)
        self.prefix_scroll = QScrollArea()
        self.prefix_scroll.setObjectName("VoicePrefixScroll")
        self.prefix_scroll.setWidgetResizable(True)
        self.prefix_scroll.setFrameShape(QFrame.NoFrame)
        self.prefix_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.prefix_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.prefix_scroll.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.prefix_scroll.setMinimumWidth(0)
        self.prefix_scroll.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.prefix_scroll.setAttribute(Qt.WA_StyledBackground, True)
        self.prefix_scroll.setStyleSheet(
            "#VoicePrefixScroll{background:transparent;border:none;}"
            "#VoicePrefixScroll > QWidget > QWidget{background:transparent;}"
        )
        try:
            self.prefix_scroll.viewport().setAutoFillBackground(False)
            self.prefix_scroll.viewport().setStyleSheet("background:transparent;")
        except Exception:
            pass
        self.prefix_host = FlowLayoutHost()
        self.prefix_host.setObjectName("VoicePrefixHost")
        self.prefix_host.setAttribute(Qt.WA_StyledBackground, True)
        self.prefix_host.setStyleSheet("#VoicePrefixHost{background:transparent;border:none;}")
        self.prefix_flow = FlowLayout(self.prefix_host, h_spacing=_PREFIX_SPACING, v_spacing=_PREFIX_SPACING)
        self.prefix_scroll.setWidget(self.prefix_host)
        sr.addWidget(self.prefix_scroll, 1)
        self._pfx_guide = _PrefixDropGuide(self.prefix_host)
        self._pfx_preview = None
        self._pfx_drag_sym = None
        self._pfx_grab = None
        self._pfx_drag_card = None
        self.symbol_row.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        cve_r.addWidget(self.symbol_row, 1)
        self._refresh_symbol_chips()
        self._restyle_sep()
        self.tool_tabs.addTab(edit_panel, "文本编辑")

        # —— 3) 文件处理（序列 + 查重）——
        self.seq_dup = SeqDupPanel()
        self.seq_dup.setProperty("borderless", "1")
        self.seq_dup.layout().setContentsMargins(0, 0, 0, 0)
        self.seq_dup.refresh_theme()
        self.seq_dup.dup_status_changed.connect(self._log)
        try:
            self.seq_dup.prefs_changed.connect(self._mark_prefs_dirty)
        except Exception:
            pass
        self._theme_frames.append(self.seq_dup)
        self.tool_tabs.addTab(self.seq_dup, "文件检查")
        self.tool_tabs.currentChanged.connect(self._sync_tool_tab_chrome)
        self._restyle_tool_tabs()

        body.addWidget(left, 70)
        body.addWidget(right, 30)


    # ── 对外接口 ──────────────────────────────────────────────────────
    def load_text(self, text: str, append: bool = False):
        """供外部（语音录入等）把文字送入编辑器。append=False 覆盖当前内容。"""
        self._stop_speak()
        text = (text or "").strip()
        if not text:
            return
        if append:
            cur = self.editor.toPlainText().strip()
            if cur:
                text = cur + "\n\n" + text
        self._mutate_editor(text, "已接收待编辑文本")

    def focus_editor(self):
        self.editor.setFocus()
        self.editor.activateWindow()

    def editor_text(self) -> str:
        return self.editor.toPlainText()

    def set_prefs_dirty_callback(self, fn):
        """主窗口注入：有变动时安排 user.txt 落盘。"""
        self._prefs_dirty = fn

    def _refresh_undo_btns(self):
        cur = self.editor.toPlainText() if hasattr(self, "editor") else ""
        can_undo = bool(self._undo_stack) or cur != self._last_snapshot
        can_redo = bool(self._redo_stack)
        if hasattr(self, "btn_undo"):
            self.btn_undo.setEnabled(can_undo)
        if hasattr(self, "btn_redo"):
            self.btn_redo.setEnabled(can_redo)

    def _apply_editor_text(self, text: str):
        self._suspend_history = True
        try:
            self.editor.setPlainText(text)
            c = self.editor.textCursor()
            c.movePosition(QTextCursor.End)
            self.editor.setTextCursor(c)
        finally:
            self._suspend_history = False

    def _trim_undo(self):
        extra = len(self._undo_stack) - _UNDO_MAX
        if extra > 0:
            del self._undo_stack[:extra]

    def _commit_typed_undo(self):
        """输入停顿后：把停顿前的快照收成一步。"""
        if self._suspend_history or not hasattr(self, "editor"):
            return
        cur = self.editor.toPlainText()
        if cur == self._last_snapshot:
            return
        self._undo_stack.append(self._last_snapshot)
        self._trim_undo()
        self._last_snapshot = cur
        self._redo_stack.clear()
        self._refresh_undo_btns()

    def _on_editor_text_changed(self):
        if self._suspend_history:
            return
        self._mark_prefs_dirty()
        self._undo_timer.start()
        self._refresh_undo_btns()

    def _mutate_editor(self, new_text: str, log_msg: str = "", select=None):
        """工具按钮改正文：先落下正在打的字，再记一步后写入。"""
        self._undo_timer.stop()
        self._commit_typed_undo()
        cur = self.editor.toPlainText()
        if new_text == cur:
            if log_msg:
                self._log(log_msg)
            return
        self._undo_stack.append(cur)
        self._trim_undo()
        self._redo_stack.clear()
        self._apply_editor_text(new_text)
        if select:
            n = len(new_text)
            s = max(0, min(int(select[0]), n))
            e = max(s, min(int(select[1]), n))
            qt_start = len(new_text[:s].encode("utf-16-le", errors="surrogatepass")) // 2
            qt_length = len(new_text[s:e].encode("utf-16-le", errors="surrogatepass")) // 2
            c = self.editor.textCursor()
            c.setPosition(qt_start)
            c.setPosition(qt_start + qt_length, QTextCursor.KeepAnchor)
            self.editor.setTextCursor(c)
            self.editor.ensureCursorVisible()
        self._last_snapshot = new_text
        self._refresh_undo_btns()
        self._mark_prefs_dirty()
        if log_msg:
            self._log(log_msg)

    def _undo_editor(self):
        self._undo_timer.stop()
        cur = self.editor.toPlainText()
        if cur != self._last_snapshot:
            self._redo_stack.append(cur)
            self._apply_editor_text(self._last_snapshot)
            self._refresh_undo_btns()
            self._mark_prefs_dirty()
            self._log("已撤销")
            return
        if not self._undo_stack:
            return
        self._redo_stack.append(cur)
        prev = self._undo_stack.pop()
        self._last_snapshot = prev
        self._apply_editor_text(prev)
        self._refresh_undo_btns()
        self._mark_prefs_dirty()
        self._log("已撤销")

    def _redo_editor(self):
        if not self._redo_stack:
            return
        self._undo_timer.stop()
        self._commit_typed_undo()
        cur = self.editor.toPlainText()
        nxt = self._redo_stack.pop()
        self._undo_stack.append(cur)
        self._trim_undo()
        self._last_snapshot = nxt
        self._apply_editor_text(nxt)
        self._refresh_undo_btns()
        self._mark_prefs_dirty()
        self._log("已重做")

    # ── 主题 ──────────────────────────────────────────────────────────
    def _restyle_tool_tabs(self):
        self.tool_tabs.setStyleSheet(
            fmt(VIDEO_TAB_QSS) + "\nQTabWidget#VideoTabWidget::pane { padding: 4px; }"
        )
        self._sync_tool_tab_chrome()

    def _sync_tool_tab_chrome(self, *_):
        tabs = self.tool_tabs
        tabs.setProperty("firstSelected", "true" if tabs.currentIndex() == 0 else "false")
        style = tabs.style()
        style.unpolish(tabs)
        style.polish(tabs)
        tabs.update()

    def _restyle_sep(self):
        # 占位高度仍用 _SEP_PX，只把可见线画成 1px，不改布局间距
        color = tk("border")
        qss = (
            "QFrame#TextEditorHSep{"
            f"background:transparent;border:none;border-top:1px solid {color};"
            f"max-height:{_SEP_PX}px;min-height:{_SEP_PX}px;}}"
        )
        for sep in getattr(self, "_h_seps", []) or []:
            sep.setStyleSheet(qss)

    def _refresh_history_icons(self):
        self.btn_undo.setIcon(make_glyph_icon("↶", px=22, color=tk("text")))
        self.btn_redo.setIcon(make_glyph_icon("↷", px=22, color=tk("text")))

    def refresh_theme(self, *_):
        self._restyle_tool_tabs()
        for t in self._theme_titles:
            restyle_card_title(t)
        panel = getattr(self, "seq_dup", None)
        if panel is not None and hasattr(panel, "refresh_theme"):
            try:
                panel.refresh_theme()
            except Exception:
                pass
        for c in self._func_cards:
            restyle_card_frame(c)
        self._restyle_sep()
        prev = getattr(self, "symbol_preview", None)
        if prev is not None:
            prev.update()
        for w in self._prefix_cards():
            w.apply_theme()
        self._paint_speak_btns()
        self._refresh_history_icons()

    # ── 日志 ──────────────────────────────────────────────────────────
    def _log(self, text: str):
        self.log.addItem(text)
        self.log.scrollToBottom()

    # ── 编辑工具 ──────────────────────────────────────────────────────
    def _replace_text(self):
        target = self.inp_find_text.text()
        if not target:
            return
        replacement = self.inp_replace_text.text()
        if target == replacement:
            self._log("查找内容与替换内容相同")
            return

        raw = self.editor.toPlainText()
        cursor = self.editor.textCursor()
        selected = cursor.hasSelection()
        start, end = _selected_text_offsets(cursor) if selected else (0, len(raw))
        source = raw[start:end]
        count = source.count(target)
        scope = "选区" if selected else "全文"
        if not count:
            self._log(f"{scope}中没有找到匹配内容")
            return

        changed = source.replace(target, replacement)
        selection = (start, start + len(changed)) if selected else None
        self._mutate_editor(
            raw[:start] + changed + raw[end:],
            f"已在{scope}替换 {count} 处",
            select=selection,
        )

    def _on_clear_editor(self):
        if not (self.editor.toPlainText() or "").strip():
            self._log("编辑区为空")
            return
        self._stop_speak()
        self._mutate_editor("", "已清空编辑区")

    def _copy_text(self):
        text = self.editor.toPlainText().strip()
        if not text:
            self._log("编辑区为空，未复制")
            return
        QApplication.clipboard().setText(text)
        self._log("已复制到剪贴板")

    def _keep_version_tag(self) -> str:
        try:
            title = self.window().windowTitle() or ""
        except Exception:
            title = ""
        found = re.search(r"v\d+(?:\.\d+)+", title)
        return found.group(0) if found else _KEEP_VER_FALLBACK

    def _public_save_root(self) -> str:
        prefs = None
        try:
            prefs = getattr(self.window(), "_user_prefs", None)
        except Exception:
            prefs = None
        from utils.app_paths import public_save_base
        return public_save_base(prefs if isinstance(prefs, dict) else None)

    def _quick_keep(self):
        """把编辑区原文存到公共目录根：桌面助手v9.19_20261022_14.35_01.txt。"""
        raw = self.editor.toPlainText()
        if not (raw or "").strip():
            self._log("编辑区为空，未保存")
            return
        try:
            root = self._public_save_root()
        except Exception:
            log.exception("读取公共目录失败")
            self._log("公共目录不可用，未保存")
            return
        if not root or not os.path.isdir(root):
            self._log("公共目录不可用，未保存")
            return
        now = datetime.now()
        prefix = (
            f"桌面助手{self._keep_version_tag()}_"
            f"{now.strftime('%Y%m%d')}_{now.strftime('%H.%M')}_"
        )
        for n in range(1, 100):
            name = f"{prefix}{n:02d}.txt"
            path = os.path.join(root, name)
            try:
                with open(path, "x", encoding="utf-8") as fp:
                    fp.write(raw)
            except FileExistsError:
                continue
            except OSError as e:
                log.exception("速存文本写入失败 path=%s", path)
                self._log(f"保存失败：{e}")
                return
            self._log(f"已速存文本：{name}")
            return
        self._log("这一分钟已有 99 个文件，未保存")

    def _selection_span(self):
        """选区在纯文本里的 [start, end)。对不上就返回 None。"""
        raw = self.editor.toPlainText()
        cur = self.editor.textCursor()
        if not cur.hasSelection():
            return None
        start, end = cur.selectionStart(), cur.selectionEnd()
        picked = cur.selectedText().replace("\u2029", "\n")
        if 0 <= start <= end <= len(raw) and raw[start:end] == picked:
            return start, end, picked
        if not picked:
            return None
        idx = raw.find(picked, max(0, start - 2))
        if idx < 0:
            idx = raw.find(picked)
        if idx < 0:
            return None
        return idx, idx + len(picked), picked

    def _set_tr_buttons(self, enabled: bool):
        for btn in (getattr(self, "btn_tr_en", None), getattr(self, "btn_tr_zh", None)):
            if btn is not None:
                btn.setEnabled(enabled)

    def _finish_translate(self):
        self._tr_busy = False
        self._tr_worker = None
        self._set_tr_buttons(True)

    def _translate_editor(self, direction: str):
        """译为英 / 译为中。勾上「选中文本」只译选区，否则译全文。"""
        if self._tr_busy:
            self._log("正在翻译，请稍候")
            return
        direction = "zh-en" if direction == "zh-en" else "en-zh"
        doing = "正在译为英文…" if direction == "zh-en" else "正在译为中文…"
        done = "已译为英文" if direction == "zh-en" else "已译为中文"
        raw = self.editor.toPlainText()
        selection_only = bool(self.chk_tr_selection.isChecked())
        if selection_only:
            span = self._selection_span()
            if span is None:
                self._log("没有选中文本")
                return
            start, end, src = span
            if not src.strip():
                self._log("选中内容为空，未翻译")
                return
        else:
            if not (raw or "").strip():
                self._log("编辑区为空，未翻译")
                return
            start, end, src = 0, len(raw), raw
        pieces = split_translate_pieces(src)
        bodies = [text for kind, text in pieces if kind == "body"]
        if not bodies:
            self._log("选中内容为空，未翻译" if selection_only else "编辑区为空，未翻译")
            return
        try:
            from utils.translate_opus import make_translate_worker, opus_direction_ready
        except Exception as e:
            log.exception("加载翻译接口失败")
            self._log(f"翻译失败：{e}")
            return
        if not opus_direction_ready(direction):
            self._log("还没有 Hy-MT2-1.8B，无法翻译")
            return

        self._tr_busy = True
        self._tr_req += 1
        req = self._tr_req
        self._set_tr_buttons(False)
        self._log(doing)
        try:
            Worker = make_translate_worker()
            worker = Worker(bodies, direction, force_cpu=False, parent=self, glossary=False)
        except Exception as e:
            log.exception("创建翻译任务失败")
            self._finish_translate()
            self._log(f"翻译失败：{e}")
            return
        self._tr_worker = worker

        def _ok(outs, _direction, rid=req, snap=(start, end, src, pieces, selection_only, done)):
            if rid != self._tr_req:
                return
            st, ed, source, parts, sel_only, done_msg = snap
            try:
                cur = self.editor.toPlainText()
                if cur[st:ed] != source:
                    self._log("编辑区已改动，译文未写入")
                    return
                new_src, changed, failed = join_translated_pieces(parts, outs)
                if changed == 0 and failed == 0:
                    self._log("译文与原文相同")
                    return
                if changed == 0:
                    self._log("翻译失败：没有得到译文")
                    return
                new_full = cur[:st] + new_src + cur[ed:]
                msg = done_msg if not failed else f"{done_msg}，有 {failed} 段没译出来"
                select = (st, st + len(new_src)) if sel_only else None
                self._mutate_editor(new_full, msg, select=select)
            except Exception as e:
                log.exception("写入译文失败")
                self._log(f"翻译失败：{e}")
            finally:
                self._finish_translate()

        def _err(msg, _direction, rid=req):
            if rid != self._tr_req:
                return
            text = str(msg or "翻译失败").strip() or "翻译失败"
            self._log(f"翻译失败：{text}")
            self._finish_translate()

        worker.finished_ok.connect(_ok)
        worker.finished_err.connect(_err)
        try:
            worker.start()
        except Exception as e:
            log.exception("启动翻译失败")
            self._finish_translate()
            self._log(f"翻译失败：{e}")

    def _cancel_translate(self):
        self._tr_req += 1
        worker = self._tr_worker
        self._tr_worker = None
        self._tr_busy = False
        self._set_tr_buttons(True)
        if worker is None:
            return
        try:
            worker.finished_ok.disconnect()
            worker.finished_err.disconnect()
        except Exception:
            pass
        try:
            if worker.isRunning():
                worker.requestInterruption()
        except Exception:
            pass

    def _collapse_to_one_paragraph(self):
        """删除编辑区全部换行符，整理成连续一段（针对 GPT 爱断行）。"""
        raw = self.editor.toPlainText()
        if not (raw or "").strip():
            self._log("编辑区为空，无需整理")
            return
        one = (
            raw.replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\u2028", "\n")
            .replace("\u2029", "\n")
            .replace("\n", "")
        )
        if one == raw:
            self._log("编辑区已无换行，无需整理")
            return
        self._mutate_editor(one, "已整理成1段（已去除全部换行）")

    def _remove_blank_lines(self):
        """删除编辑区里的空行（仅空白的行也去掉），保留有内容的行与顺序。"""
        raw = self.editor.toPlainText()
        if not (raw or "").strip():
            self._log("编辑区为空，无需删除空行")
            return
        norm = (
            raw.replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\u2028", "\n")
            .replace("\u2029", "\n")
        )
        lines = norm.split("\n")
        if not any(not ln.strip() for ln in lines):
            self._log("编辑区没有空行，无需删除")
            return
        kept = [ln for ln in lines if ln.strip()]
        self._mutate_editor("\n".join(kept), "已删除空行")

    def _smart_wrap_lines(self):
        """按句末标点断行。段落内的硬换行先接上，空行仍隔开段落。"""
        raw = self.editor.toPlainText()
        if not (raw or "").strip():
            self._log("编辑区为空，无需智能换行")
            return
        new = smart_line_breaks(raw)
        if new == raw:
            self._log("编辑区已按句换行，无需再处理")
            return
        n = sum(1 for ln in new.split("\n") if ln.strip())
        self._mutate_editor(new, f"已智能换行（共 {n} 行）")

    def _remove_right_margin(self):
        raw = self.editor.toPlainText()
        new = remove_right_margin_breaks(raw)
        if new == raw:
            self._log("未发现需要接回的硬换行")
            return
        removed = raw.count("\n") - new.count("\n")
        self._mutate_editor(new, f"已去右边界（接回 {removed} 处换行）")

    def _remove_whitespace(self):
        raw = self.editor.toPlainText()
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            start, end = _selected_text_offsets(cursor)
            cleaned = raw[:start] + remove_intrasentence_whitespace(raw[start:end]) + raw[end:]
        else:
            cleaned = remove_intrasentence_whitespace(raw)
        if cleaned == raw:
            self._log("未发现需要去除的句内空格或换行")
            return
        self._mutate_editor(cleaned, "已去除句内空格和换行")

    def _smart_layout(self):
        raw = self.editor.toPlainText()
        cursor = self.editor.textCursor()
        selected = cursor.hasSelection()
        line_range = None
        if selected:
            start, end = _selected_text_offsets(cursor)
            line_range = raw.count("\n", 0, start), raw.count("\n", 0, end - 1)
        if not raw.strip():
            self._log("没有可排版的文本")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("智能排版")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("处理选中的段落" if selected else "处理全文"))
        blank_line = QCheckBox("段间统一留一个空行")
        blank_line.setChecked(True)
        indent = QCheckBox("段首缩进两个全角空格")
        indent.setChecked(True)
        trim_end = QCheckBox("清理行尾空格和制表符")
        for option in (blank_line, indent, trim_end):
            layout.addWidget(option)
        buttons = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Cancel)
        apply_medium_button(buttons.button(QDialogButtonBox.Apply)).setText("应用")
        apply_medium_button(buttons.button(QDialogButtonBox.Cancel)).setText("取消")
        buttons.button(QDialogButtonBox.Apply).clicked.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec_() != QDialog.Accepted:
            return

        formatted = format_paragraphs(
            raw,
            blank_line=blank_line.isChecked(),
            indent=indent.isChecked(),
            trim_end=trim_end.isChecked(),
            line_range=line_range,
        )
        message = "已完成智能排版" if formatted != raw else "文本已符合所选排版"
        self._mutate_editor(formatted, message)

    @staticmethod
    def _index_to_letters(i: int) -> str:
        """0 → A，25 → Z，26 → AA。"""
        n = int(i) + 1
        chars = []
        while n > 0:
            n, rem = divmod(n - 1, 26)
            chars.append(chr(ord("A") + rem))
        return "".join(reversed(chars))

    @classmethod
    def _chinese_number(cls, number: int, keep_leading_one: bool = False) -> str:
        for scale, unit in ((100000000, "亿"), (10000, "万")):
            if number >= scale:
                high, low = divmod(number, scale)
                text = cls._chinese_number(high) + unit
                if low:
                    text += ("零" if low < scale // 10 else "") + cls._chinese_number(low, True)
                return text

        digits = "零一二三四五六七八九"
        parts = []
        zero_pending = False
        for place, unit in ((1000, "千"), (100, "百"), (10, "十"), (1, "")):
            digit, number = divmod(number, place)
            if not digit:
                zero_pending = bool(parts)
                continue
            if zero_pending:
                parts.append("零")
                zero_pending = False
            if parts or digit != 1 or place != 10 or keep_leading_one:
                parts.append(digits[digit])
            parts.append(unit)
        return "".join(parts)

    @classmethod
    def _number_prefix(cls, number: int, scheme: str) -> str:
        if scheme == "chinese":
            return cls._chinese_number(number) + "、"
        if scheme in ("upper", "lower"):
            letters = cls._index_to_letters(number - 1)
            return (letters if scheme == "upper" else letters.lower()) + ". "
        if scheme == "circled":
            for first, last, glyph in ((1, 20, "①"), (21, 35, "㉑"), (36, 50, "㊱")):
                if first <= number <= last:
                    return chr(ord(glyph) + number - first) + " "
            return f"({number}) "
        if scheme == "comma":
            return f"{number}、"
        if scheme == "brackets":
            return f"({number}) "
        if scheme == "right_bracket":
            return f"{number}) "
        return f"{number}. "

    def _num_start_value(self) -> int:
        """起始序号：正整数；空/非法回退为 1。"""
        w = getattr(self, "inp_num_start", None)
        raw = ""
        try:
            raw = (w.text() if w is not None else "") or ""
        except Exception:
            raw = ""
        raw = raw.strip()
        if not raw:
            return 1
        try:
            n = int(raw)
        except Exception:
            return 1
        if n < 1:
            return 1
        return n

    def _add_line_numbers(self, scheme: str):
        """按所选样式重新编号，空行是否计数由复选框决定。"""
        raw = self.editor.toPlainText()
        ignore_blank = self.chk_prefix_skip_blank.isChecked()
        if not raw:
            self._log("编辑区为空，无需加序号")
            return
        start = self._num_start_value()
        numbered = []
        number = start
        for line in raw.split("\n"):
            if ignore_blank and _PREFIX_BLANK.fullmatch(line):
                numbered.append(line)
                continue
            text = _NUMBER_PREFIX.sub("", line.lstrip(), count=1).strip()
            if _PREFIX_BLANK.fullmatch(text):
                if ignore_blank:
                    numbered.append(text)
                    continue
                text = ""
            numbered.append(self._number_prefix(number, scheme) + text)
            number += 1
        self._mutate_editor("\n".join(numbered), f"已加序号（共 {number - start} 行）")

    def _apply_selected_symbol(self):
        if self._selected_symbol is not None:
            self._add_symbol_prefix(self._selected_symbol)

    def _add_symbol_prefix(self, symbol: str):
        """把已选符号加到每一行的开头。"""
        raw = self.editor.toPlainText()
        ignore_blank = self.chk_prefix_skip_blank.isChecked()
        if not raw:
            self._log("编辑区为空，无需加符号")
            return
        sym_re = re.compile(r"^\s*" + re.escape(symbol))
        out = []
        n_apply = 0
        for line in raw.split("\n"):
            blank = bool(_PREFIX_BLANK.fullmatch(line))
            if ignore_blank and blank:
                out.append(line)
                continue
            existing = sym_re.match(line)
            if existing:
                remainder = line[existing.end():]
                if ignore_blank and _PREFIX_BLANK.fullmatch(remainder):
                    out.append(remainder)
                    continue
                out.append(line)
            elif blank:
                out.append(symbol)
            else:
                out.append(symbol + line)
            n_apply += 1
        shown = symbol if len(symbol) <= 8 else symbol[:8] + "…"
        self._mutate_editor(
            "\n".join(out),
            f"已加符号前缀「{shown}」（共 {n_apply} 行）",
        )

    # ── 朗读（男 / 女，复用阅读器 CosyReader）────────────────────────
    def _speak_source(self) -> str:
        """有选区读选区，否则从光标读到文末。"""
        span = self._selection_span()
        if span is not None:
            return span[2] or ""
        raw = self.editor.toPlainText() if hasattr(self, "editor") else ""
        cur = self.editor.textCursor()
        pos = int(cur.position())
        if pos < 0:
            pos = 0
        if pos >= len(raw):
            return ""
        return raw[pos:]

    def _on_speak_btn(self, which: str):
        speak = getattr(self, "_speak", None)
        if speak is None:
            return
        speak.toggle(which, self._speak_source())

    def _stop_speak(self, *_):
        speak = getattr(self, "_speak", None)
        if speak is not None:
            speak.stop()

    def _paint_speak_btns(self):
        speak = getattr(self, "_speak", None)
        on = bool(speak and speak.on)
        paused = bool(speak and speak.paused)
        voice = getattr(speak, "voice", "男") if speak is not None else "男"
        for btn, which in ((getattr(self, "btn_speak_m", None), "男"),
                           (getattr(self, "btn_speak_f", None), "女")):
            if btn is None:
                continue
            style_speak_button(btn, on and voice == which, paused=paused)
        esc = getattr(self, "_esc_speak", None)
        if esc is not None:
            esc.setEnabled(on)

    def _on_speak_failed(self, message: str):
        tip = (message or "").strip() or "无法朗读"
        for btn in (getattr(self, "btn_speak_m", None), getattr(self, "btn_speak_f", None)):
            if btn is not None:
                btn.setToolTip(tip)
        timer = getattr(self, "_speak_tip_timer", None)
        if timer is not None:
            timer.start()

    def _restore_speak_tips(self):
        tip = getattr(self, "_speak_tip", "有选区读选区，否则从光标读到文末")
        for btn in (getattr(self, "btn_speak_m", None), getattr(self, "btn_speak_f", None)):
            if btn is not None:
                btn.setToolTip(tip)

    def hideEvent(self, e):
        super().hideEvent(e)
        # 只在本页真正离开界面时停（切走、关闭），避免改按钮样式等瞬时 Hide 把朗读掐掉。
        if not self.isVisible():
            self._stop_speak()

    # ── 前缀符号历史卡片 ─────────────────────────────────────────────
    def _apply_word_wrap(self, checked: bool):
        """开：按窗口宽度折行；关：一行到底，出现横向滚动条。"""
        if checked:
            self.editor.setLineWrapMode(QTextEdit.WidgetWidth)
            self.editor.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        else:
            self.editor.setLineWrapMode(QTextEdit.NoWrap)
            self.editor.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

    def _on_word_wrap_toggled(self, checked: bool):
        self._apply_word_wrap(checked)
        self._mark_prefs_dirty()


    def export_check_dup_settings(self) -> dict:
        panel = getattr(self, "seq_dup", None)
        if panel is None:
            return {}
        try:
            return dict(panel.export_settings() or {})
        except Exception:
            return {}

    def apply_check_dup_settings(self, d: dict) -> None:
        panel = getattr(self, "seq_dup", None)
        if panel is None:
            return
        try:
            panel.apply_settings(d or {})
        except Exception:
            pass


    def on_sequence_fill_done(self, **kwargs):
        panel = getattr(self, "seq_dup", None)
        if panel is not None and hasattr(panel, "on_sequence_fill_done"):
            return panel.on_sequence_fill_done(**kwargs)

    def stop_background_checks(self):
        panel = getattr(self, "seq_dup", None)
        if panel is not None and hasattr(panel, "stop_background_checks"):
            try:
                panel.stop_background_checks()
            except Exception:
                pass

    def shutdown(self):
        self._stop_speak()
        speak = getattr(self, "_speak", None)
        if speak is not None:
            try:
                speak.close()
            except Exception:
                pass
        self._cancel_translate()
        self.stop_background_checks()

    def export_settings(self) -> dict:
        """写入 user.txt 的文本编辑段。"""
        wrap = True
        chk = getattr(self, "chk_word_wrap", None)
        if chk is not None:
            wrap = bool(chk.isChecked())
        return {
            "symbol_history": [str(s) for s in self._symbol_history],
            "prefix_skip_blank": self.chk_prefix_skip_blank.isChecked(),
            "word_wrap": wrap,
            "editor_text": self.editor.toPlainText() if hasattr(self, "editor") else "",
            "tool_order": self.tool_grid.order(),
        }

    def apply_settings(self, data: dict):
        """从 user.txt 恢复前缀历史、换行开关、编辑区正文和工具顺序。"""
        data = data or {}
        self.tool_grid.restore_order(data.get("tool_order"))
        if "prefix_skip_blank" in data:
            blocked = self.chk_prefix_skip_blank.blockSignals(True)
            self.chk_prefix_skip_blank.setChecked(bool(data["prefix_skip_blank"]))
            self.chk_prefix_skip_blank.blockSignals(blocked)
        if "word_wrap" in data:
            wrap = bool(data.get("word_wrap"))
            chk = getattr(self, "chk_word_wrap", None)
            if chk is not None and chk.isChecked() != wrap:
                chk.blockSignals(True)
                chk.setChecked(wrap)
                chk.blockSignals(False)
                self._apply_word_wrap(wrap)
        if "editor_text" in data and hasattr(self, "editor"):
            text = data.get("editor_text")
            text = text if isinstance(text, str) else str(text or "")
            if self.editor.toPlainText() != text:
                self._stop_speak()
                self._suspend_history = True
                try:
                    self.editor.setPlainText(text)
                finally:
                    self._suspend_history = False
            self._last_snapshot = text
            self._undo_stack = []
            self._redo_stack = []
            self._refresh_undo_btns()
        raw = data.get("symbol_history")
        if isinstance(raw, list):
            hist = []
            for s in raw:
                s = str(s)
                if s and s not in hist:
                    hist.append(s)
                    if len(hist) >= _PREFIX_MAX:
                        break
            self._symbol_history = hist
        if hasattr(self, "prefix_host"):
            self._refresh_symbol_chips()

    def _mark_prefs_dirty(self):
        fn = self._prefs_dirty
        if callable(fn):
            try:
                fn()
            except Exception:
                pass

    def _remember_symbol(self, symbol: str):
        """把「这次实际用过的」前缀收进历史（去重、保持添加顺序）。"""
        symbol = symbol or ""
        if not symbol:
            return
        if symbol in self._symbol_history:
            return
        self._symbol_history.append(symbol)
        if len(self._symbol_history) > _PREFIX_MAX:
            del self._symbol_history[: len(self._symbol_history) - _PREFIX_MAX]
        self._refresh_symbol_chips()
        self._mark_prefs_dirty()

    def _save_current_symbol_as_card(self):
        """「+」：把前缀编辑区当前内容直接存成一张前缀卡。"""
        symbol = self.inp_symbol.text() or ""
        if not symbol:
            self._log("前缀编辑区是空的，无法存成前缀卡")
            self.inp_symbol.setFocus()
            return
        existed = symbol in self._symbol_history
        self._remember_symbol(symbol)
        if existed:
            self._log(f"「{symbol}」已在前缀卡中")
        else:
            self._log(f"已把当前前缀「{symbol}」存成前缀卡")

    def _use_symbol(self, symbol: str):
        """单击卡片直接添加前缀，同时填入自定义框以便编辑。"""
        self.inp_symbol.setText(symbol)
        self._select_symbol(symbol)
        self._apply_selected_symbol()

    def _on_symbol_input_changed(self, text: str):
        if text != self._selected_symbol:
            self._clear_symbol_selection()

    def _prefix_cards(self):
        host = getattr(self, "prefix_host", None)
        if host is None:
            return []
        return [w for w in host.children() if isinstance(w, _PrefixCard)]

    def _select_symbol(self, symbol: str):
        self._selected_symbol = symbol or None
        self._sync_card_selection()

    def _clear_symbol_selection(self):
        if not self._selected_symbol:
            return
        self._selected_symbol = None
        self._sync_card_selection()

    def _sync_card_selection(self):
        sel = self._selected_symbol
        self.btn_add_symbol.setEnabled(sel is not None)
        for w in self._prefix_cards():
            w.set_selected(w.symbol == sel)

    def _click_keeps_symbol_sel(self, w) -> bool:
        cur = w
        while cur is not None:
            if isinstance(cur, _PrefixCard) or cur in (
                self.btn_add_symbol, self.inp_symbol, self.chk_prefix_skip_blank, self.editor,
            ):
                return True
            if cur is self:
                break
            cur = cur.parentWidget()
        return False

    def _install_blank_click_filter(self):
        app = QApplication.instance()
        if app is None:
            return
        app.installEventFilter(self)
        self._click_filter_on = True
        try:
            self.destroyed.connect(self._teardown_click_filter)
        except Exception:
            pass

    def _teardown_click_filter(self, *_):
        if not getattr(self, "_click_filter_on", False):
            return
        app = QApplication.instance()
        if app is not None:
            try:
                app.removeEventFilter(self)
            except Exception:
                pass
        self._click_filter_on = False

    def eventFilter(self, obj, ev):
        try:
            if (
                ev.type() == QEvent.MouseButtonPress
                and ev.button() == Qt.LeftButton
                and self._selected_symbol
                and self.isVisible()
                and not getattr(self, "_pfx_drag_sym", None)
            ):
                w = None
                try:
                    w = QApplication.widgetAt(ev.globalPos())
                except Exception:
                    w = obj if isinstance(obj, QWidget) else None
                if w is not None and not self._click_keeps_symbol_sel(w):
                    self._clear_symbol_selection()
        except Exception:
            pass
        return super().eventFilter(obj, ev)

    def _remove_symbol(self, symbol: str):
        if symbol in self._symbol_history:
            if self._selected_symbol == symbol:
                self._selected_symbol = None
            self._symbol_history.remove(symbol)
            self._refresh_symbol_chips()
            self._mark_prefs_dirty()
            self._log(f"已删除前缀卡片「{symbol}」")

    def _refresh_symbol_chips(self):
        """重建可直接应用的自定义前缀卡。"""
        if not hasattr(self, "prefix_host"):
            return
        self._pfx_clear_drag_visual()
        flow = self.prefix_flow
        while flow.count():
            it = flow.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.symbol_row.setVisible(bool(self._symbol_history))
        if not self._symbol_history:
            self._selected_symbol = None
            self._sync_card_selection()
            return
        from PyQt5.QtGui import QFontMetrics
        fm = QFontMetrics(self.prefix_host.font())
        for i, symbol in enumerate(self._symbol_history):
            text = str(symbol)
            accent = _PREFIX_PALETTE[i % len(_PREFIX_PALETTE)]
            card = _PrefixCard(text, accent, self, self.prefix_host)
            w = max(_PREFIX_CARD_MIN_W, fm.horizontalAdvance(text) + 36)
            card.setFixedWidth(min(w, _PREFIX_CARD_MAX_W))
            card.clicked.connect(lambda s=text: self._use_symbol(s))
            card.remove_requested.connect(lambda s=text: self._remove_symbol(s))
            flow.addWidget(card)
        flow.invalidate()
        self.prefix_host.updateGeometry()
        if self._selected_symbol and self._selected_symbol not in self._symbol_history:
            self._selected_symbol = None
        self._sync_card_selection()

    # ── 卡片拖拽（影子跟手 + 卡间虚线）────────────────────────────
    def _pfx_clear_drag_visual(self):
        g = getattr(self, "_pfx_guide", None)
        if g is not None:
            g.hide()
        pv = getattr(self, "_pfx_preview", None)
        if pv is not None:
            pv.hide()
            pv.setParent(None)
            pv.deleteLater()
        self._pfx_preview = None
        self._pfx_drag_sym = None
        self._pfx_grab = None
        self._pfx_drag_card = None

    def _begin_prefix_drag(self, card):
        self._pfx_clear_drag_visual()
        sym = str(card.symbol or "")
        if sym not in self._symbol_history:
            return
        self._pfx_drag_sym = sym
        self._pfx_drag_card = card
        try:
            card.grabMouse()
        except Exception:
            pass
        pm = card.grab()
        pv = _PrefixDragPreview(pm, self)
        self._pfx_preview = pv
        try:
            pos = card.mapTo(self, QPoint(0, 0))
        except Exception:
            pos = QPoint(0, 0)
        pv.move(pos)
        pv.show()
        pv.raise_()
        grab = QPoint(pm.width() // 2, pm.height() // 2)
        self._pfx_grab = grab
        press = getattr(card, "_press", None)
        if press is not None:
            self._pfx_move_visual(press)

    def _move_prefix_drag(self, gpos):
        sym = self._pfx_drag_sym
        if not sym:
            return
        pv = self._pfx_preview
        if pv is not None:
            grab = self._pfx_grab or QPoint(pv.width() // 2, pv.height() // 2)
            pv.move(self.mapFromGlobal(gpos) - grab)
        self._pfx_move_visual(gpos)

    def _pfx_drop_location(self, gpos):
        viewport = self.prefix_scroll.viewport()
        if not viewport.rect().contains(viewport.mapFromGlobal(gpos)):
            return None
        return self.prefix_flow.drop_location(self.prefix_host.mapFromGlobal(gpos))

    def _pfx_move_visual(self, gpos):
        location = self._pfx_drop_location(gpos)
        guide = self._pfx_guide
        if location is None:
            guide.hide()
            return
        _, position = location
        x = max(0, min(position.x() - _PREFIX_GUIDE_W // 2, self.prefix_host.width() - _PREFIX_GUIDE_W))
        guide.move(x, position.y())
        guide.show()
        guide.raise_()

    def _end_prefix_drag(self, gpos):
        sym = self._pfx_drag_sym
        card = self._pfx_drag_card
        if card is not None:
            try:
                card.releaseMouse()
            except Exception:
                pass
            card.setCursor(Qt.OpenHandCursor)
        location = self._pfx_drop_location(gpos)
        if sym and location is not None:
            self._commit_prefix_order(sym, location[0])
        self._pfx_clear_drag_visual()

    def _commit_prefix_order(self, symbol: str, insert: int):
        if symbol not in self._symbol_history:
            return
        old = self._symbol_history.index(symbol)
        if old == insert or old == insert - 1:
            self._refresh_symbol_chips()
            return
        self._symbol_history.pop(old)
        if old < insert:
            insert -= 1
        insert = max(0, min(insert, len(self._symbol_history)))
        self._symbol_history.insert(insert, symbol)
        self._refresh_symbol_chips()
        self._mark_prefs_dirty()
