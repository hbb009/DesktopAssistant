# pages/page_ratio_calc.py
# 比例计算（实用工具 · 子页）：无内层套框，分区用「空隙 + 实线 + 空隙」分隔

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFrame, QButtonGroup, QScrollArea, QSizePolicy, QApplication,
    QGridLayout, QTextEdit, QPlainTextEdit, QAbstractButton,
)
from PyQt5.QtCore import Qt, QTimer, QSize, QEvent
from PyQt5.QtGui import QFont
from styles.style_all import (
    install_card_title,
    restyle_card_title,
    make_card,
    CARD_BOTTOM_GAP,
    CARD_TITLE_BODY_GAP,
    theme,
    tk,
)
from utils.flow_layout import FlowLayout
import ast
import math

# 分隔线粗细（px）
_SEP_PX = 3

# 键盘按下后按键高亮的时长（ms）。轻点靠这个自动熄灭；按住不放由 KeyRelease 提前熄
_PAD_FLASH_MS = 150


def _make_h_sep() -> QFrame:
    """横向实线分隔：主题色由 PageRatioCalc._restyle_seps 统一刷。"""
    f = QFrame()
    f.setObjectName("RatioHSep")
    f.setFrameShape(QFrame.NoFrame)
    f.setFixedHeight(_SEP_PX)
    f.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    f.setAttribute(Qt.WA_StyledBackground, True)
    return f


def _make_v_sep() -> QFrame:
    """纵向实线分隔：夹在左右列之间。"""
    f = QFrame()
    f.setObjectName("RatioVSep")
    f.setFrameShape(QFrame.NoFrame)
    f.setFixedWidth(_SEP_PX)
    f.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
    f.setAttribute(Qt.WA_StyledBackground, True)
    return f


class PageRatioCalc(QWidget):
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

    def __init__(self):
        super().__init__()
        # 与主内容卡同底：避免滚动区内默认 QWidget 灰底「露馅」
        self.setObjectName("PageRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)

        def _typo(w: QLabel, name: str):
            w.setProperty("typo", name)
            w.style().unpolish(w); w.style().polish(w)

        # 分区标题（install_card_title 内联色，主题切换需 restyle）
        self._theme_titles = []
        # 横/竖实线分隔（主题切换重刷颜色）
        self._h_seps = []
        self._v_seps = []

        # 外层滚动区，内容较多时可垂直滚动；横向滚动条强制关闭——
        # 窗口变窄时靠内部各处的 FlowLayout 自动换行，不允许左右拖动。
        scroll = QScrollArea(self)
        scroll.setObjectName("RatioScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setAttribute(Qt.WA_StyledBackground, True)
        # 全局有 QWidget{background:#eef2f7/#0b1124}，滚动宿主若不透明会
        # 在白/深主内容卡里露出一块明显「脏底」（尤其右列 stretch 空白区）
        scroll.setStyleSheet(
            "QScrollArea#RatioScroll{background:transparent;border:none;}"
            "QScrollArea#RatioScroll > QWidget > QWidget{background:transparent;}"
        )
        try:
            scroll.viewport().setAutoFillBackground(False)
            scroll.viewport().setStyleSheet("background:transparent;")
        except Exception:
            pass
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        container = QWidget()
        container.setObjectName("RatioScrollHost")
        container.setAttribute(Qt.WA_StyledBackground, True)
        container.setStyleSheet(
            "#RatioScrollHost{background:transparent;border:none;}"
        )
        scroll.setWidget(container)

        # 顶层：左（比例 + 金额 + 汉字）· 竖线 · 右（简易计算）
        # Tab pane 已是最外框，内部分区全部 borderless，不再套矩形卡
        root = QHBoxLayout(container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)  # 列间「空隙+竖线+空隙」显式控制
        root.setAlignment(Qt.AlignTop)

        # ═══════════════ 左列 ═══════════════
        left = QVBoxLayout()
        left.setAlignment(Qt.AlignTop)
        left.setSpacing(0)  # 分区间距由「空隙+实线+空隙」显式控制
        root.addLayout(left, 2)

        # 内部状态文案（用于各操作提示，如"计算完成"/"已复制"），不再显示绿灯+文字条
        self.status = QLabel("🟢 就绪")
        _typo(self.status, "body")

        # ── 比例换算（3–5 字 · 无外框） ──────────────────
        sec_ratio = make_card("CardRatioCalc", borderless=True)
        ratio_box = QVBoxLayout(sec_ratio)
        # 底边距归零：预设数值到分隔线的空隙只由下面 left.addSpacing 控制
        ratio_box.setContentsMargins(0, 0, 0, 0)
        ratio_box.setSpacing(CARD_TITLE_BODY_GAP)
        self._theme_titles.append(install_card_title(sec_ratio, ratio_box, "比例换算"))
        left.addWidget(sec_ratio)

        self.a, self.b, self.c, self.d = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        for w, ph, obj in [
            (self.a, 'A', 'RatioA'), (self.b, 'B', 'RatioB'),
            (self.c, 'C', 'RatioC'), (self.d, 'D', 'RatioD'),
        ]:
            w.setPlaceholderText(ph); w.setObjectName(obj)
            w.setFixedHeight(40); w.setAlignment(Qt.AlignCenter)
            # 与「时区汇率」金额框同款数字字体（雅黑 15 / 600）
            self._apply_ratio_amount_font(w)
        # ABCD 均可输入；以 A:B 为比例标准：改 A/B/C → 算 D，改 D → 反推 C
        self._ratio_lock = False
        self._ratio_src = "c"  # 最近编辑的字段：a/b/c/d
        self._cd_target = None  # 分辨率芯片写入目标：c / d / None

        self._debounce = QTimer(self); self._debounce.setSingleShot(True)
        self._debounce.setInterval(600)
        self._debounce.timeout.connect(self._ratio_calc)
        for name, w in (("a", self.a), ("b", self.b), ("c", self.c), ("d", self.d)):
            w.textChanged.connect(lambda _=None, n=name: self._on_ratio_edited(n))
            w.returnPressed.connect(lambda n=name: self._ratio_commit(n))
            w.editingFinished.connect(lambda n=name: self._ratio_on_editing_finished(n))
        self.c.installEventFilter(self)
        self.d.installEventFilter(self)
        # 用应用级过滤实现「点空白取消选中」；对象销毁时 Qt 会自动卸掉 filter。
        # 不要在 destroyed 里再 removeEventFilter(self)，那时 C++ 对象已删，会 RuntimeError。
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        # 比例芯片放在 A/B/C/D 上方（字号 +1）
        chips_row = FlowLayout(h_spacing=8, v_spacing=8)
        ratio_box.addLayout(chips_row)
        presets = [
            ("1:1",  1,  1, 1536),
            ("2:3",  2,  3, 1536),
            ("3:4",  3,  4, 1536),
            ("9:16", 9, 16, 1536),
            ("9:21", 9, 21, 2048),
        ]
        self._chip_group = QButtonGroup(self); self._chip_group.setExclusive(True)
        self._chip_group.buttonToggled.connect(self._on_ratio_chip_toggled)
        for name, ra, rb, long_side in presets:
            short = round(long_side * rb / ra) if ra >= rb else round(long_side * ra / rb)
            w_px  = long_side if ra >= rb else short
            h_px  = short     if ra >= rb else long_side
            b = QPushButton(name); b.setCheckable(True)
            b.setObjectName(f"RatioChip_{name.replace(':', '_')}")
            b.setProperty("ratioChip", True)
            b.setProperty("ra", int(ra))
            b.setProperty("rb", int(rb))
            b.setToolTip(f"{name}（{w_px}×{h_px}）")
            _cf = b.font(); _cf.setPixelSize(14); _cf.setBold(True); b.setFont(_cf)
            self._style_ratio_chip(b)
            b.clicked.connect(lambda _, a=ra, bb=rb, label=name, c=long_side:
                              self._apply_ratio(a, bb, label, c))
            self._chip_group.addButton(b); chips_row.addWidget(b)

        ratio_box.addSpacing(8)

        # 输入区：左 A/B + 交换｜右 C/D + 复制
        inputs_row = QHBoxLayout()
        inputs_row.setSpacing(8)
        ratio_box.addLayout(inputs_row)

        ab_fields = QVBoxLayout()
        ab_fields.setSpacing(6)
        r_a = QHBoxLayout()
        labA = QLabel("A"); _typo(labA, "body")
        r_a.addWidget(labA); r_a.addWidget(self.a)
        ab_fields.addLayout(r_a)
        r_b = QHBoxLayout()
        labB = QLabel("B"); _typo(labB, "body")
        r_b.addWidget(labB); r_b.addWidget(self.b)
        ab_fields.addLayout(r_b)
        inputs_row.addLayout(ab_fields)

        btn_swap = QPushButton("A&&B\n交换")
        btn_swap.setProperty("role", "nav")
        btn_swap.setStyleSheet(
            "QPushButton { text-align: center; padding: 4px 8px; }"
        )
        btn_swap.style().unpolish(btn_swap); btn_swap.style().polish(btn_swap)
        btn_swap.clicked.connect(self._swap)
        inputs_row.addWidget(btn_swap, 0, Qt.AlignVCenter)

        inputs_row.addStretch(1)

        cd_fields = QVBoxLayout()
        cd_fields.setSpacing(6)
        r_c = QHBoxLayout()
        labC = QLabel("C"); _typo(labC, "body")
        r_c.addWidget(labC); r_c.addWidget(self.c)
        btn_copy_c = QPushButton("复制")
        btn_copy_c.setProperty("role", "nav")
        btn_copy_c.style().unpolish(btn_copy_c); btn_copy_c.style().polish(btn_copy_c)
        btn_copy_c.clicked.connect(self._copy_c)
        r_c.addWidget(btn_copy_c)
        cd_fields.addLayout(r_c)
        r_d = QHBoxLayout()
        labD = QLabel("D"); _typo(labD, "body")
        r_d.addWidget(labD); r_d.addWidget(self.d)
        btn_copy_d = QPushButton("复制")
        btn_copy_d.setProperty("role", "nav")
        btn_copy_d.style().unpolish(btn_copy_d); btn_copy_d.style().polish(btn_copy_d)
        btn_copy_d.clicked.connect(self._copy_d)
        r_d.addWidget(btn_copy_d)
        cd_fields.addLayout(r_d)
        inputs_row.addLayout(cd_fields)

        # 常用分辨率 → 写入当前选中的 C 或 D
        ratio_box.addSpacing(8)
        self._tip_res = QLabel()
        _typo(self._tip_res, "caption")
        self._tip_res.setWordWrap(True)
        self._tip_res.setTextFormat(Qt.RichText)
        self._res_tip_warn = False
        self._refresh_res_tip()
        ratio_box.addWidget(self._tip_res)
        res_row = FlowLayout(h_spacing=8, v_spacing=8)
        ratio_box.addLayout(res_row)
        res_vals = (
            512, 640, 768, 832, 896, 1024, 1152, 1280,
            1344, 1408, 1536, 1664, 1792, 1920, 2048,
            2304, 2560, 2880, 3072, 3200, 3456, 3584,
            3840, 4096,
        )
        for v in res_vals:
            b = QPushButton(str(v))
            b.setObjectName("RatioResChip")
            b.setProperty("role", "nav")
            b.setToolTip(f"写入选中的 C/D = {v}")
            # 字号必须写进 QSS：role=nav 的全局 font-size 会盖掉 setPixelSize
            _rf = b.font(); _rf.setPixelSize(12); b.setFont(_rf)
            b.setStyleSheet(
                "QPushButton#RatioResChip {"
                "  font-size: 12px;"
                "  padding: 5px 4px;"
                "  min-height: 0px;"
                "}"
            )
            b.style().unpolish(b); b.style().polish(b)
            b.clicked.connect(lambda _=False, n=v: self._apply_res_value(n))
            res_row.addWidget(b)
        self._style_cd_target()

        # 空隙 + 实线 + 空隙（预设数值 → 分隔线 = 6px）
        left.addSpacing(6)
        h1 = _make_h_sep()
        self._h_seps.append(h1)
        left.addWidget(h1)
        left.addSpacing(10)

        # ── 金额大写 ──────────────────────────────────────
        sec_case = make_card("CardRatioCase", borderless=True)
        case_box = QVBoxLayout(sec_case)
        case_box.setContentsMargins(0, 0, 0, CARD_BOTTOM_GAP)
        case_box.setSpacing(CARD_TITLE_BODY_GAP)
        self._theme_titles.append(install_card_title(sec_case, case_box, "金额大写"))
        left.addWidget(sec_case)

        row_in = QHBoxLayout()
        lbl_small = QLabel("小写金额："); _typo(lbl_small, "body"); lbl_small.setFixedWidth(72)
        self.amount_input = QLineEdit()
        self.amount_input.setPlaceholderText("输入数字金额，如 1688.99")
        self.amount_input.setFixedHeight(40)
        _af = self.amount_input.font(); _af.setPixelSize(16); self.amount_input.setFont(_af)
        btn_convert = QPushButton("转换")
        btn_convert.setProperty("role", "primary")
        btn_convert.style().unpolish(btn_convert); btn_convert.style().polish(btn_convert)
        btn_convert.setFixedHeight(40)
        btn_convert.clicked.connect(self._convert_amount)
        self.amount_input.returnPressed.connect(self._convert_amount)
        row_in.addWidget(lbl_small); row_in.addWidget(self.amount_input); row_in.addWidget(btn_convert)
        case_box.addLayout(row_in)

        row_out = QHBoxLayout()
        lbl_big = QLabel("大写金额："); _typo(lbl_big, "body"); lbl_big.setFixedWidth(72)
        self.amount_output = QLineEdit()
        self.amount_output.setReadOnly(True); self.amount_output.setFixedHeight(40)
        _of = self.amount_output.font(); _of.setPixelSize(16); self.amount_output.setFont(_of)
        btn_copy_amt = QPushButton("复制")
        btn_copy_amt.setProperty("role", "nav")
        btn_copy_amt.style().unpolish(btn_copy_amt); btn_copy_amt.style().polish(btn_copy_amt)
        btn_copy_amt.setFixedHeight(40)
        btn_copy_amt.clicked.connect(self._copy_amount)
        row_out.addWidget(lbl_big); row_out.addWidget(self.amount_output); row_out.addWidget(btn_copy_amt)
        case_box.addLayout(row_out)

        # 空隙 + 实线 + 空隙
        left.addSpacing(10)
        h2 = _make_h_sep()
        self._h_seps.append(h2)
        left.addWidget(h2)
        left.addSpacing(10)

        # ── 汉字速查 ──────────────────────────────────────
        sec_chars = make_card("CardRatioChars", borderless=True)
        chars_box = QVBoxLayout(sec_chars)
        chars_box.setContentsMargins(0, 0, 0, CARD_BOTTOM_GAP)
        chars_box.setSpacing(CARD_TITLE_BODY_GAP)
        self._theme_titles.append(install_card_title(sec_chars, chars_box, "汉字速查"))
        left.addWidget(sec_chars)

        hint = QLabel("点击单字即可复制到剪贴板"); _typo(hint, "muted")
        chars_box.addWidget(hint)

        # 一字流排：不在「玖」后强制换行，窗口变窄时由 FlowLayout 自然折行
        CHARS = [
            "零", "壹", "贰", "叁", "肆", "伍", "陆", "柒", "捌", "玖",
            "拾", "佰", "仟", "万", "亿", "元", "角", "分", "整", "负",
        ]
        self._char_status = QLabel("")
        _typo(self._char_status, "muted")

        chars_flow = FlowLayout(h_spacing=6, v_spacing=6)
        chars_box.addLayout(chars_flow)
        for ch in CHARS:
            btn = QPushButton(ch)
            btn.setFixedSize(46, 42)
            btn.setObjectName("CharChip")
            _cf = btn.font(); _cf.setPixelSize(17); _cf.setBold(False); btn.setFont(_cf)
            btn.clicked.connect(lambda _, c=ch: self._copy_char(c))
            chars_flow.addWidget(btn)
        chars_box.addWidget(self._char_status)

        left.addStretch(1)

        # 列间：空隙 + 竖线 + 空隙
        root.addSpacing(12)
        v_mid = _make_v_sep()
        self._v_seps.append(v_mid)
        root.addWidget(v_mid)
        root.addSpacing(12)

        # ═══════════════ 右列 ═══════════════
        right = QVBoxLayout()
        right.setSpacing(0)
        right.setAlignment(Qt.AlignTop)
        root.addLayout(right, 1)

        # ── 计算器（对齐 Windows 计算器 · 标准模式） ──────────
        # 顶对齐：右列 setAlignment(AlignTop)，本区排在最上，往下依次是分隔线 + 公式计算
        sec_pad = make_card("CardRatioCalcPad", borderless=True)
        right.addWidget(sec_pad)
        self._build_calc_pad(sec_pad)

        # 空隙 + 实线 + 空隙（计算器 → 分隔线 = 10px）
        right.addSpacing(10)
        h3 = _make_h_sep()
        self._h_seps.append(h3)
        right.addWidget(h3)
        right.addSpacing(10)

        # ── 公式计算 ──────────────────────────────────────
        sec_calc = make_card("CardRatioSimple", borderless=True)
        calc_box = QVBoxLayout(sec_calc)
        calc_box.setContentsMargins(0, 0, 0, CARD_BOTTOM_GAP)
        calc_box.setSpacing(CARD_TITLE_BODY_GAP)
        self._theme_titles.append(install_card_title(sec_calc, calc_box, "公式计算"))
        right.addWidget(sec_calc)

        self.calc_expr = QLineEdit()
        self.calc_expr.setPlaceholderText("输入表达式，例如：(1920/1080)*1.5")
        self.calc_expr.setFixedHeight(40); self.calc_expr.setAlignment(Qt.AlignLeft)
        _ef = self.calc_expr.font(); _ef.setPixelSize(16); self.calc_expr.setFont(_ef)
        calc_box.addWidget(self.calc_expr)

        btn_calc_row = QHBoxLayout()
        calc_box.addLayout(btn_calc_row)
        btn_eval = QPushButton("计算"); btn_eval.setProperty("role", "primary")
        btn_eval.style().unpolish(btn_eval); btn_eval.style().polish(btn_eval)
        btn_eval.clicked.connect(self._calc_eval); btn_calc_row.addWidget(btn_eval)
        btn_clear = QPushButton("清空")
        btn_clear.clicked.connect(lambda: (self.calc_expr.clear(), self.calc_result.clear()))
        btn_calc_row.addWidget(btn_clear); btn_calc_row.addStretch(1)

        self.calc_result = QLineEdit(); self.calc_result.setReadOnly(True)
        self.calc_result.setPlaceholderText("结果")
        self.calc_result.setFixedHeight(40); self.calc_result.setAlignment(Qt.AlignLeft)
        _rf = self.calc_result.font(); _rf.setPixelSize(16); self.calc_result.setFont(_rf)
        calc_box.addWidget(self.calc_result)
        self.calc_expr.returnPressed.connect(self._calc_eval)

        right.addStretch(1)

        # 首刷分隔线色 + 计算器外观 + 订阅主题
        self._restyle_seps()
        self._style_calc_pad()
        theme.changed.connect(self._apply_theme)

    def _restyle_seps(self):
        """横/竖分隔线：3px 实线、随主题 border 色。"""
        color = tk("border")
        h_qss = (
            "QFrame#RatioHSep{"
            f"background:{color};border:none;"
            f"max-height:{_SEP_PX}px;min-height:{_SEP_PX}px;}}"
        )
        v_qss = (
            "QFrame#RatioVSep{"
            f"background:{color};border:none;"
            f"max-width:{_SEP_PX}px;min-width:{_SEP_PX}px;}}"
        )
        for f in self._h_seps:
            f.setStyleSheet(h_qss)
        for f in self._v_seps:
            f.setStyleSheet(v_qss)

    def _apply_theme(self, *_args):
        for lbl in self._theme_titles:
            restyle_card_title(lbl)
        self._restyle_seps()
        self._style_calc_pad()

    # ═══════════════ 计算器（Windows 标准计算器） ═══════════════
    # 布局照抄 Win10/Win11「计算器 · 标准」：记忆行 + 5×4 键盘 + 上下两行显示。
    # 与「公式计算」互不相干：独立状态机、独立显示框，互不写入对方的输入。
    _PAD_SYM = {"+": "+", "-": "−", "*": "×", "/": "÷"}

    _PAD_MEM_KEYS = (
        ("MC", "mc", "清除记忆 (Memory Clear)"),
        ("MR", "mr", "读取记忆 (Memory Recall)"),
        ("MS", "ms", "存储记忆 (Memory Store)"),
        ("M+", "madd", "记忆加 (Memory Add)"),
        ("M−", "msub", "记忆减 (Memory Subtract)"),
    )

    _PAD_KEYS = (
        ("%",  "pct", "百分比 (%)"),
        ("CE", "ce", "清除当前输入 (Delete)"),
        ("C",  "clear", "全部清除 (Esc)"),
        ("⌫",  "back", "退格 (Backspace)"),

        ("1/x", "inv", "倒数 (R)"),
        ("x²",  "sqr", "平方 (Q)"),
        ("√x",  "sqrt", "平方根 (@)"),
        ("÷",   "/", "除"),

        ("7", "7", None), ("8", "8", None), ("9", "9", None), ("×", "*", "乘"),
        ("4", "4", None), ("5", "5", None), ("6", "6", None), ("−", "-", "减"),
        ("1", "1", None), ("2", "2", None), ("3", "3", None), ("+", "+", "加"),
        ("±",  "neg", "正负号"), ("0", "0", None), (".", "dot", "小数点"),
        ("=",  "eq", "等于 (Enter)"),
    )

    # 键盘符号 → 按键动作。用 event.text() 匹配，顺带覆盖 Shift 组合与别的键盘布局
    _PAD_TEXT_MAP = {
        ".": "dot", ",": "dot",
        "+": "+", "-": "-", "*": "*", "×": "*", "/": "/", "÷": "/",
        "%": "pct", "％": "pct",
        "@": "sqrt", "^": "sqr",
        "r": "inv", "R": "inv", "q": "sqr", "Q": "sqr",
    }

    def _build_calc_pad(self, sec: QFrame):
        pad_box = QVBoxLayout(sec)
        pad_box.setContentsMargins(0, 0, 0, CARD_BOTTOM_GAP)
        pad_box.setSpacing(CARD_TITLE_BODY_GAP)
        self._theme_titles.append(install_card_title(sec, pad_box, "计算器"))

        # ── 状态机（同 Win 计算器：entry 当前输入 / acc 左值 / op 待运算符）──
        self._pad_entry = "0"   # 当前输入串（显示什么就是什么）
        self._pad_acc = None    # 记忆的左值
        self._pad_op = None     # 待执行运算符 '+'/'-'/'*'/'/'
        self._pad_fresh = True  # 下一次数字是否重开一串
        self._pad_mem = None    # 记忆值
        self._pad_err = False   # 是否停在错误提示上
        self._pad_hist = ""     # 等号后残留的算式（显示区上行）

        # 键盘按压反馈：动作 → 按钮，键盘操作时把这个键点亮一下
        self._pad_key_btns = {}
        self._pad_flash_btn = None
        self._pad_flash_timer = QTimer(self)
        self._pad_flash_timer.setSingleShot(True)
        self._pad_flash_timer.timeout.connect(self._pad_flash_clear)

        # 整块挂在一个 host 上，QSS 只刷 host 一处，避免盖掉卡片自身的描边样式
        host = QWidget()
        host.setObjectName("RatioCalcPad")
        self._pad_host = host
        pad_l = QVBoxLayout(host)
        pad_l.setContentsMargins(0, 0, 0, 0)
        pad_l.setSpacing(4)
        pad_box.addWidget(host)

        # ── 显示区：上小算式 + 下大数值，右侧对齐 ──
        disp = QFrame()
        disp.setObjectName("RatioPadDisplay")
        disp.setAttribute(Qt.WA_StyledBackground, True)
        disp.setFixedHeight(66)
        disp_l = QVBoxLayout(disp)
        disp_l.setContentsMargins(10, 4, 10, 4)
        disp_l.setSpacing(0)
        self._pad_expr = QLabel("")
        self._pad_expr.setObjectName("RatioPadExpr")
        self._pad_expr.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        self._pad_value = QLabel("0")
        self._pad_value.setObjectName("RatioPadValue")
        self._pad_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        disp_l.addWidget(self._pad_expr)
        disp_l.addWidget(self._pad_value)
        pad_l.addWidget(disp)

        # ── 记忆行 ──
        mem_row = QHBoxLayout()
        mem_row.setSpacing(4)
        self._pad_mem_btns = {}
        for text, act, tip in self._PAD_MEM_KEYS:
            b = self._make_pad_key(text, act, 24, tip)
            mem_row.addWidget(b)
            self._pad_mem_btns[act] = b
        pad_l.addLayout(mem_row)

        # ── 键盘区：6 行 × 4 列 ──
        grid_host = QWidget()
        grid_host.setObjectName("RatioPadGrid")
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        for i, (text, act, tip) in enumerate(self._PAD_KEYS):
            r, c = divmod(i, 4)
            grid.addWidget(self._make_pad_key(text, act, 32, tip), r, c)
        pad_l.addWidget(grid_host)

        self._pad_keys_ready = True
        self._pad_refresh()

    @staticmethod
    def _pad_kind(act: str) -> str:
        """按键分类：决定 QSS 里的字号/配色。"""
        if act == "eq":
            return "eq"
        if act in ("mc", "mr", "ms", "madd", "msub"):
            return "mem"
        if act in ("+", "-", "*", "/", "neg", "dot") or act.isdigit():
            return "key"
        return "fn"

    def _make_pad_key(self, text: str, act: str, height: int, tip: str = None) -> QPushButton:
        """计算器按键：NoFocus，键盘操作不依赖焦点落在哪个键上。"""
        b = QPushButton(text)
        b.setObjectName("RatioPadKey")
        b.setProperty("padKey", self._pad_kind(act))
        b.setFixedHeight(height)
        b.setFocusPolicy(Qt.NoFocus)
        b.setCursor(Qt.PointingHandCursor)
        if tip:
            b.setToolTip(tip)
        b.clicked.connect(lambda _, a=act: self._pad_key(a))
        self._pad_key_btns[act] = b
        return b

    def _style_calc_pad(self):
        """计算器配色：整体跟主题走；等号用品牌蓝。"""
        host = getattr(self, "_pad_host", None)
        if host is None:
            return
        try:
            key_bg = tk("row_bg")
            edge = tk("border_2")
            txt = tk("text_strong")
            mut = tk("text_dim")
            disp_bg = tk("input_deep")
            acc = tk("accent")
            acc_hover = tk("accent_hover")
            hover = tk("hover_veil")
        except Exception:
            return
        host.setStyleSheet(
            "QFrame#RatioPadDisplay{"
            f"  background:{disp_bg};"
            f"  border:1px solid {edge};"
            "  border-radius:8px;"
            "}"
            "QLabel#RatioPadExpr{"
            f"  color:{mut}; background:transparent; font-size:12px; font-weight:500;"
            "}"
            "QLabel#RatioPadValue{"
            f"  color:{txt}; background:transparent;"
            "}"
            "QPushButton#RatioPadKey{"
            f"  background:{key_bg};"
            f"  border:1px solid {edge};"
            f"  color:{txt};"
            "  border-radius:6px;"
            "  font-weight:600;"
            "  padding:0px;"
            "  min-height:0px;"
            "}"
            "QPushButton#RatioPadKey:hover{"
            f"  background:{hover};"
            f"  border-color:{acc};"
            "}"
            "QPushButton#RatioPadKey:pressed{"
            f"  border-color:{acc};"
            "}"
            "QPushButton#RatioPadKey:disabled{"
            f"  color:{mut};"
            "  background:transparent;"
            "}"
            "QPushButton#RatioPadKey[padKey=\"key\"]{"
            "  font-size:15px;"
            "  min-height:30px;"
            "}"
            "QPushButton#RatioPadKey[padKey=\"fn\"]{"
            "  font-size:13px;"
            "  min-height:30px;"
            "}"
            "QPushButton#RatioPadKey[padKey=\"mem\"]{"
            f"  font-size:12px;"
            f"  color:{mut};"
            "  background:transparent;"
            "  min-height:22px;"
            "}"
            "QPushButton#RatioPadKey[padKey=\"mem\"]:hover{"
            f"  color:{txt};"
            f"  background:{hover};"
            f"  border-color:{acc};"
            "}"
            "QPushButton#RatioPadKey[padKey=\"eq\"]{"
            f"  background:{acc};"
            f"  border-color:{acc};"
            "  color:#ffffff;"
            "  font-size:17px;"
            "  min-height:30px;"
            "}"
            "QPushButton#RatioPadKey[padKey=\"eq\"]:hover{"
            f"  background:{acc_hover};"
            f"  border-color:{acc_hover};"
            "}"
            "QPushButton#RatioPadKey[padKey=\"eq\"]:disabled{"
            f"  background:{acc};"
            "  color:#ffffff;"
            "}"
            # 键盘按压反馈：这条必须排在最后——同优先级下靠后的规则胜出，
            # 才能盖住上面各 padKey 分层的配色。
            "QPushButton#RatioPadKey[padFlash=\"true\"]{"
            f"  background:{acc};"
            f"  border-color:{acc};"
            "  color:#ffffff;"
            "}"
            "QPushButton#RatioPadKey[padFlash=\"true\"]:disabled{"
            f"  background:{acc_hover};"
            f"  border-color:{acc_hover};"
            "  color:#ffffff;"
            "}"
        )
        self._pad_refresh()

    # ──────── 计算器：显示格式化 ────────
    @staticmethod
    def _pad_fmt(x) -> str:
        """整数不带小数点；过小/过大转科学计数（对齐 Win 计算器）。"""
        if isinstance(x, float):
            if x != x or x in (float("inf"), float("-inf")):
                raise ValueError("非有限数")
        x = float(x)
        if x == 0:
            return "0"
        ax = abs(x)
        if ax >= 1e16 or ax < 1e-10:
            mant, exp = f"{x:.10E}".split("E")
            mant = mant.rstrip("0").rstrip(".")
            ei = int(exp)
            return f"{mant}e{'+' if ei >= 0 else '-'}{abs(ei)}"
        if abs(x - round(x)) < 1e-9:
            return str(int(round(x)))
        s = f"{x:.10f}".rstrip("0").rstrip(".")
        return s or "0"

    @staticmethod
    def _pad_apply(a: float, op: str, b: float) -> float:
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            if b == 0:
                raise ZeroDivisionError
            return a / b
        raise ValueError("不支持的运算符")

    def _pad_refresh(self):
        """刷新显示区 + 记忆键可用态。等号残留算式优先于「左值 + 运算符」。"""
        if self._pad_op is not None and self._pad_acc is not None and not self._pad_err:
            self._pad_expr.setText(
                f"{self._pad_fmt(self._pad_acc)} {self._PAD_SYM[self._pad_op]}"
            )
        else:
            self._pad_expr.setText(self._pad_hist)
        self._pad_value.setText(self._pad_entry or "0")
        self._pad_fit_value_font(self._pad_entry or "0")
        has_mem = self._pad_mem is not None
        for act in ("mc", "mr", "madd", "msub"):
            b = self._pad_mem_btns.get(act)
            if b is not None:
                b.setEnabled(has_mem)

    def _pad_fit_value_font(self, text: str):
        """结果位数多时自动降字号，免得顶出显示框（同 Win 计算器的自适应显示）。"""
        n = len(text)
        px = 26 if n <= 9 else (20 if n <= 13 else 16)
        if getattr(self, "_pad_value_px", None) == px:
            return
        self._pad_value_px = px
        f = self._pad_value.font()
        f.setPixelSize(px)
        f.setWeight(QFont.Bold)
        self._pad_value.setFont(f)

    def _pad_err_show(self, msg: str):
        """出错：显示提示、清掉挂起算式，下一次数字从 0 重开（同 Win）。"""
        self._pad_err = True
        self._pad_entry = msg
        self._pad_hist = ""
        self._pad_acc = None
        self._pad_op = None
        self._pad_fresh = True
        self.status.setText(f"❌ {msg}")

    def _pad_key(self, act: str):
        """按键统一入口：任何异常都收敛成计算器自己的错误提示，不往上抛。"""
        try:
            self._pad_act(act)
        except ZeroDivisionError:
            self._pad_err_show("不能除以 0")
        except OverflowError:
            self._pad_err_show("数值超出范围")
        except Exception:
            self._pad_err_show("输入有误")
        try:
            self._pad_refresh()
        except RuntimeError:
            pass

    def _pad_act(self, act: str):
        # 只有等号会把算式留在显示区上行
        if act != "eq":
            self._pad_hist = ""
        e = self._pad_entry

        # ── 数字 ──
        if act.isdigit():
            if self._pad_err:
                self._pad_entry, self._pad_err = "0", False
            if self._pad_fresh:
                self._pad_entry, self._pad_fresh = act, False
            else:
                digits = self._pad_entry.lstrip("-").replace(".", "")
                if len(digits) < 16:  # Win 计算器同款上限，防长串撑爆显示区
                    if self._pad_entry in ("0", "-0"):
                        self._pad_entry = self._pad_entry[:-1] + act
                    else:
                        self._pad_entry += act
            return

        # ── 小数点 ──
        if act == "dot":
            if self._pad_err:
                self._pad_entry, self._pad_err = "0", False
            if self._pad_fresh:
                self._pad_entry, self._pad_fresh = "0.", False
            elif "." not in self._pad_entry:
                self._pad_entry += "."
            return

        # ── CE / C / 退格 ──
        if act == "ce":
            self._pad_entry, self._pad_fresh, self._pad_err = "0", True, False
            return
        if act == "clear":
            self._pad_entry, self._pad_fresh = "0", True
            self._pad_acc, self._pad_op, self._pad_err = None, None, False
            return
        if act == "back":
            if self._pad_err or self._pad_fresh:
                return
            s = self._pad_entry[:-1]
            self._pad_entry = "0" if s in ("", "-") else s
            return

        # ── 记忆 ──
        if act == "mc":
            self._pad_mem = None
            return
        if act == "mr":
            if self._pad_mem is not None:
                self._pad_entry = self._pad_fmt(self._pad_mem)
                self._pad_fresh, self._pad_err = True, False
            return
        if self._pad_err:
            return  # 错误态下 M+/M-/MS 无意义
        if act == "ms":
            self._pad_mem = float(e)
            self._pad_fresh = True
            return
        if act == "madd":
            self._pad_mem = (self._pad_mem or 0.0) + float(e)
            self._pad_fresh = True
            return
        if act == "msub":
            self._pad_mem = (self._pad_mem or 0.0) - float(e)
            self._pad_fresh = True
            return

        # ── 运算符 ──
        if act in ("+", "-", "*", "/"):
            if self._pad_op is not None and not self._pad_fresh:
                # 连按：先把挂起的那一步算掉
                self._pad_acc = self._pad_apply(self._pad_acc, self._pad_op, float(e))
                self._pad_entry = self._pad_fmt(self._pad_acc)
            elif self._pad_op is None:
                self._pad_acc = float(e)
            # _pad_op 已有值且刚按过运算符 → 直接替换成新运算符
            self._pad_op = act
            self._pad_fresh = True
            return

        # ── 等号 ──
        if act == "eq":
            if self._pad_op is not None and self._pad_acc is not None:
                lhs = self._pad_fmt(self._pad_acc)
                res = self._pad_apply(self._pad_acc, self._pad_op, float(e))
                out = self._pad_fmt(res)
                self._pad_entry = out
                self._pad_hist = f"{lhs} {self._PAD_SYM[self._pad_op]} {e} ="
                self.status.setText(f"🟢 {self._pad_hist} {out}")
            self._pad_acc, self._pad_op, self._pad_fresh = None, None, True
            return

        # ── 百分比 / 单函数 / 正负号 ──
        if act == "pct":
            cur = float(e)
            # 加减号按「左值 × 当前值」折算成加/减项，乘除号直接除 100（同 Win）
            if self._pad_op in ("+", "-") and self._pad_acc is not None:
                cur = self._pad_acc * cur / 100.0
            else:
                cur = cur / 100.0
            self._pad_entry, self._pad_fresh = self._pad_fmt(cur), True
            return
        if act == "inv":
            self._pad_entry = self._pad_fmt(1.0 / float(e))
            self._pad_fresh = True
            return
        if act == "sqr":
            self._pad_entry = self._pad_fmt(float(e) ** 2)
            self._pad_fresh = True
            return
        if act == "sqrt":
            v = float(e)
            if v < 0:
                # 直接报具体原因，别让 _pad_key 的兜底把它盖成「输入有误」
                self._pad_err_show("负数无平方根")
                return
            self._pad_entry = self._pad_fmt(math.sqrt(v))
            self._pad_fresh = True
            return
        if act == "neg":
            if float(e) == 0:
                self._pad_entry = "0"
            elif self._pad_entry.startswith("-"):
                self._pad_entry = self._pad_entry[1:]
            else:
                self._pad_entry = "-" + self._pad_entry
            return

        # 未知动作静默忽略：键盘映射万一漏了某个字符，也不该把显示打成错误提示
        return

    def _pad_flash(self, act: str):
        """键盘按下时把对应键点亮（同 Windows 计算器：按键会闪一下）。"""
        btn = getattr(self, "_pad_key_btns", {}).get(act)
        if btn is None:
            return
        try:
            if self._pad_flash_btn is btn:
                # 同一个键连按：只把倒计时续上，不重复 polish
                self._pad_flash_timer.start(_PAD_FLASH_MS)
                return
            self._pad_flash_clear()
            btn.setProperty("padFlash", True)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.update()
            self._pad_flash_btn = btn
            self._pad_flash_timer.start(_PAD_FLASH_MS)
        except RuntimeError:
            self._pad_flash_btn = None

    def _pad_flash_clear(self, btn=None):
        """熄灭按键反馈。btn 省略时清掉当前亮着的那个。

        定时器只是「轻点」兜底：按住不放时由 KeyRelease 立刻熄灭。
        """
        target = btn if btn is not None else self._pad_flash_btn
        if target is None:
            return
        if btn is None:
            self._pad_flash_timer.stop()
        try:
            target.setProperty("padFlash", False)
            target.style().unpolish(target)
            target.style().polish(target)
            target.update()
        except RuntimeError:
            pass
        if target is self._pad_flash_btn:
            self._pad_flash_btn = None

    def _pad_handle_key(self, ev) -> bool:
        """键盘直控计算器。返回 True 表示已吞掉这个按键。

        只要求「本页是当前页」。焦点在哪都行——点侧栏切过来之后焦点还留在
        侧栏按钮上，若额外要求焦点在页内，按键会被全部放行、计算器毫无反应。
        例外只有两类：
          · 焦点在文本框里（A/B/C/D、金额、公式表达式）→ 放行，打字优先；
          · 焦点在本页自己的按钮上且按的是回车/空格 → 放行，否则「复制」这类
            按钮用键盘就点不动了。
        """
        act = self._pad_key_action(ev)
        if act is None:
            return False
        self._pad_key(act)
        self._pad_flash(act)
        return True

    def _pad_key_action(self, ev):
        """按键 → 动作。返回 None 表示这个键不该由计算器处理。"""
        if not getattr(self, "_pad_keys_ready", False) or not self.isVisible():
            return None
        obj = QApplication.focusWidget()
        if isinstance(obj, (QLineEdit, QTextEdit, QPlainTextEdit)):
            return None
        if obj is not None and obj.window() is not self.window():
            return None  # 焦点在别的顶层窗（如模态对话框）里，不插手
        in_page = obj is not None and (obj is self or self.isAncestorOf(obj))

        mods = ev.modifiers()
        # Alt/Ctrl/Meta 一律放行：全局热键（Alt+1 等）不能被计算器吃掉
        if mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            if mods & Qt.ControlModifier and not mods & Qt.AltModifier \
                    and ev.key() == Qt.Key_L:
                return "clear"  # Ctrl+L = 全部清除（同 Win 计算器）
            return None

        key = ev.key()
        if key == Qt.Key_Escape:
            return "clear"
        if key == Qt.Key_Backspace:
            return "back"
        if key == Qt.Key_Delete:
            return "ce"
        if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            if in_page and isinstance(obj, QAbstractButton):
                return None
            return "eq"  # 空格也当等号：本页按键都是 NoFocus，空格闲着也是闲着
        # 带 Shift 的数字不当作数字（Shift+1 是「!」），留给下面的字符匹配
        if Qt.Key_0 <= key <= Qt.Key_9 and not mods & Qt.ShiftModifier:
            return chr(key)
        if key == Qt.Key_Period and not mods & Qt.ShiftModifier:
            return "dot"
        # 兜底看实际字符：覆盖 Shift+= → +、Shift+5 → %、数字小键盘符号、别的键盘布局
        return self._PAD_TEXT_MAP.get(ev.text() or "")

    def _pad_key_release(self, ev) -> bool:
        """按键抬起：把该键的按压反馈立刻熄灭（按住不放时的行为）。"""
        act = self._pad_key_action(ev)
        btn = getattr(self, "_pad_key_btns", {}).get(act)
        if btn is not None and btn is self._pad_flash_btn:
            self._pad_flash_clear(btn)
        return False

    # ──────── 比例计算 ────────
    def _clear_ratio_chips(self):
        """取消所有比例预设选中，恢复普通按钮外观。"""
        group = getattr(self, "_chip_group", None)
        if group is None:
            return
        group.setExclusive(False)
        for b in group.buttons():
            if b.isChecked():
                b.setChecked(False)
            self._style_ratio_chip(b)
        group.setExclusive(True)

    def _sync_ratio_chip_selection(self):
        """手动改 A/B（或对调）后，若不再匹配当前预设，则取消选中。"""
        group = getattr(self, "_chip_group", None)
        if group is None:
            return
        checked = group.checkedButton()
        if checked is None:
            return
        try:
            ra = int(checked.property("ra"))
            rb = int(checked.property("rb"))
            a = float((self.a.text() or "").strip())
            b = float((self.b.text() or "").strip())
            if a == 0 or b == 0:
                raise ValueError
            # 交叉相乘，避免浮点除法误差
            if abs(a * rb - b * ra) > 1e-6:
                self._clear_ratio_chips()
        except Exception:
            self._clear_ratio_chips()

    def _style_ratio_chip(self, btn: QPushButton):
        """比例芯片：未选中跟 nav；选中实心蓝（内联，避免被全局 QSS 盖掉）。"""
        if btn.isChecked():
            btn.setStyleSheet(
                "QPushButton {"
                "  background: #3b82f6;"
                "  border: 1px solid #2563eb;"
                "  color: #ffffff;"
                "  font-size: 14px;"
                "  font-weight: 700;"
                "  border-radius: 6px;"
                "  padding: 4px 10px;"
                "}"
                "QPushButton:hover {"
                "  background: #2563eb;"
                "  border-color: #1d4ed8;"
                "  color: #ffffff;"
                "}"
            )
        else:
            btn.setStyleSheet(
                "QPushButton {"
                "  background: transparent;"
                "  border: 1px solid rgba(148,163,184,0.45);"
                "  color: palette(window-text);"
                "  font-size: 14px;"
                "  font-weight: 700;"
                "  border-radius: 6px;"
                "  padding: 4px 10px;"
                "}"
                "QPushButton:hover {"
                "  background: rgba(59,130,246,0.45);"
                "  border-color: #3b82f6;"
                "  color: #ffffff;"
                "}"
            )

    def _on_ratio_chip_toggled(self, btn, checked: bool):
        del checked
        # 组内任一变化时，重刷全部芯片选中态样式
        for b in self._chip_group.buttons():
            self._style_ratio_chip(b)

    def _apply_ratio_amount_font(self, inp: QLineEdit, selected: bool = None):
        """对齐「时区汇率·金额」：Microsoft YaHei UI · 15px · DemiBold。
        selected 仅用于 C/D：始终 2px 边框，选中蓝色，未选中透明描边，避免抖动。
        """
        fnt = QFont("Microsoft YaHei UI")
        if not fnt.exactMatch():
            fnt = QFont("Microsoft YaHei")
        if not fnt.exactMatch():
            fnt = QFont("Segoe UI")
        fnt.setPixelSize(15)
        fnt.setWeight(QFont.DemiBold)
        try:
            fnt.setStyleStrategy(QFont.PreferOutline | QFont.PreferQuality)
        except Exception:
            pass
        inp.setFont(fnt)
        inp.setProperty("ratioAmount", True)
        if selected is None:
            # A/B：只锁字号，不改边框
            inp.setStyleSheet(
                'QLineEdit[ratioAmount="true"], '
                'QLineEdit[ratioAmount="true"]:hover, '
                'QLineEdit[ratioAmount="true"]:focus {'
                '  font-size: 15px;'
                '  font-weight: 600;'
                "  font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI';"
                '}'
            )
        else:
            try:
                edge = tk("border")
            except Exception:
                edge = "rgba(148,163,184,0.55)"
            border = "#3b82f6" if selected else edge
            bg = "rgba(59,130,246,0.12)" if selected else "transparent"
            inp.setStyleSheet(
                'QLineEdit[ratioAmount="true"], '
                'QLineEdit[ratioAmount="true"]:hover, '
                'QLineEdit[ratioAmount="true"]:focus {'
                '  font-size: 15px;'
                '  font-weight: 600;'
                "  font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI';"
                f"  border: 2px solid {border};"
                f"  background: {bg};"
                "  padding: 0px;"
                '}'
            )


    @staticmethod
    def _fmt_num(x: float) -> str:
        """整数不带小数；否则最多 2 位，去掉尾零。"""
        if abs(x - round(x)) < 1e-9:
            return str(int(round(x)))
        s = f"{x:.2f}".rstrip("0").rstrip(".")
        return s or "0"

    def _on_ratio_edited(self, which: str):
        if self._ratio_lock:
            return
        self._ratio_src = which
        if which in ("a", "b"):
            self._sync_ratio_chip_selection()
        self._debounce.start()

    def _ratio_on_editing_finished(self, which: str):
        # 回车会先触发 returnPressed（已 commit）；这里只处理失焦
        if self._debounce.isActive():
            self._ratio_commit(which)

    def _ratio_commit(self, which: str = None):
        """回车/失焦：立刻按当前字段结算，并结束该框输入。"""
        self._debounce.stop()
        if which:
            self._ratio_src = which
        self._ratio_calc()
        box = {"a": self.a, "b": self.b, "c": self.c, "d": self.d}.get(which)
        if box is not None:
            box.clearFocus()

    def _ratio_calc(self):
        """以 A:B 为比例标准。
        · 编辑 A / B / C → D = B × C ÷ A
        · 编辑 D       → C = A × D ÷ B
        """
        if self._ratio_lock:
            return
        try:
            a = float((self.a.text() or "").strip())
            b = float((self.b.text() or "").strip())
            if a == 0 or b == 0:
                raise ZeroDivisionError
            src = getattr(self, "_ratio_src", "c") or "c"
            self._ratio_lock = True
            try:
                if src == "d":
                    d = float((self.d.text() or "").strip())
                    c = a * d / b
                    self.c.blockSignals(True)
                    self.c.setText(self._fmt_num(c))
                    self.c.blockSignals(False)
                else:
                    c = float((self.c.text() or "").strip())
                    d = b * c / a
                    self.d.blockSignals(True)
                    self.d.setText(self._fmt_num(d))
                    self.d.blockSignals(False)
            finally:
                self._ratio_lock = False
            self.status.setText("🟢 计算完成")
        except Exception:
            if getattr(self, "_ratio_src", "c") == "d":
                # 反推失败时不强制清空对方，避免误伤
                pass
            else:
                self.d.blockSignals(True)
                self.d.clear()
                self.d.blockSignals(False)
            self.status.setText("⚪ 等待输入…")
            self._ratio_lock = False

    def _swap(self):
        a_txt, b_txt = self.a.text(), self.b.text()
        self.a.blockSignals(True); self.b.blockSignals(True)
        self.a.setText(b_txt); self.b.setText(a_txt)
        self.a.blockSignals(False); self.b.blockSignals(False)
        # 交换比例后仍以 A 为标准，用当前 C 重算 D
        self._ratio_src = "c"
        self._ratio_calc()
        self._sync_ratio_chip_selection()
        self.a.setFocus()

    def _copy_d(self):
        val = self.d.text().split(".")[0]
        from PyQt5.QtCore import QMimeData
        from PyQt5.QtWidgets import QApplication
        m = QMimeData(); m.setText(val); QApplication.clipboard().setMimeData(m)
        self.status.setText("🟢 已复制D整数部分到剪贴板")

    def _copy_c(self):
        val = self.c.text().strip()
        if not val:
            self.status.setText("⚪ C 值为空，无法复制")
            return
        from PyQt5.QtCore import QMimeData
        from PyQt5.QtWidgets import QApplication
        m = QMimeData(); m.setText(val); QApplication.clipboard().setMimeData(m)
        self.status.setText("🟢 已复制C值到剪贴板")

    def _apply_ratio(self, ra: int, rb: int, name: str, c: int = 0):
        self.a.blockSignals(True); self.b.blockSignals(True)
        self.a.setText(str(ra)); self.b.setText(str(rb))
        self.a.blockSignals(False); self.b.blockSignals(False)
        if c:
            self.c.blockSignals(True)
            self.c.setText(str(c))
            self.c.blockSignals(False)
        self._ratio_src = "c"
        self._ratio_calc()
        if c:
            short = round(c * rb / ra) if ra >= rb else round(c * ra / rb)
            w_px  = c     if ra >= rb else short
            h_px  = short if ra >= rb else c
            self.status.setText(f"🟢 {name}  →  {w_px}×{h_px} px（已自动计算）")
        else:
            self.status.setText(f"🟡 已选择比例 {name}，请在 C 输入具体数值")

    def event(self, event):
        # deleteLater 路径：C++ 仍有效时先卸掉应用级 filter，避免销毁期回调踩空
        if event.type() == QEvent.DeferredDelete:
            app = QApplication.instance()
            if app is not None:
                try:
                    app.removeEventFilter(self)
                except RuntimeError:
                    pass
            # 高亮定时器不能带着 pending 回调活到销毁之后
            try:
                self._pad_flash_timer.stop()
                self._pad_flash_btn = None
            except RuntimeError:
                pass
        return super().event(event)

    def eventFilter(self, obj, event):
        # 键盘直控计算器（仅本页为当前页时生效）
        try:
            if event.type() == QEvent.KeyPress:
                if self._pad_handle_key(event):
                    return True
            elif event.type() == QEvent.KeyRelease:
                self._pad_key_release(event)
        except RuntimeError:
            return False

        # C/D 点选；点页面空白取消；分辨率芯片点击保持当前选中
        try:
            if event.type() == QEvent.MouseButtonPress and getattr(event, "button", lambda: None)() == Qt.LeftButton:
                if isinstance(obj, QWidget) and (obj is self or self.isAncestorOf(obj)):
                    hit = self._hit_cd_res(obj)
                    if hit in ("c", "d"):
                        self._set_cd_target(hit)
                    elif hit != "res":
                        self._set_cd_target(None)
        except RuntimeError:
            # 页面销毁过程中可能被点到，忽略即可
            return False
        return super().eventFilter(obj, event)

    def _hit_cd_res(self, w):
        cur = w
        while cur is not None:
            if cur is self.c:
                return "c"
            if cur is self.d:
                return "d"
            try:
                if isinstance(cur, QPushButton) and cur.objectName() == "RatioResChip":
                    return "res"
            except Exception:
                pass
            cur = cur.parentWidget()
        return None

    def _set_cd_target(self, which):
        if which not in ("c", "d"):
            which = None
        if getattr(self, "_cd_target", None) == which:
            return
        self._cd_target = which
        # 选中 C/D 后清掉蓝色警告；取消选中保留提示文案本身
        if which in ("c", "d"):
            self._res_tip_warn = False
            self._refresh_res_tip()
        self._style_cd_target()

    def _refresh_res_tip(self):
        """分辨率提示：常驻文案；未选中却点数值时追加蓝色警告。"""
        tip = getattr(self, "_tip_res", None)
        if tip is None:
            return
        base = "先选C或D再点下面数值"
        if getattr(self, "_res_tip_warn", False):
            tip.setText(
                f'{base}'
                f'<span style="color:#3b82f6;"> 没有选中C或D</span>'
            )
        else:
            tip.setText(base)

    def _style_cd_target(self):
        """C/D 选中态：始终 2px 边框，避免选中时布局抖动。"""
        target = getattr(self, "_cd_target", None)
        for name, w in (("c", self.c), ("d", self.d)):
            selected = name == target
            self._apply_ratio_amount_font(w, selected=selected)
            w.setProperty("cdSelected", bool(selected))
            w.style().unpolish(w)
            w.style().polish(w)
            w.update()

    def _apply_res_value(self, value: int):
        """常用分辨率芯片：写入当前选中的 C 或 D；未选中则提示。"""
        target = getattr(self, "_cd_target", None)
        if target not in ("c", "d"):
            self._res_tip_warn = True
            self._refresh_res_tip()
            self.status.setText("⚠ 请先点选 C 或 D，再点数值")
            return
        box = self.d if target == "d" else self.c
        box.blockSignals(True)
        box.setText(str(int(value)))
        box.blockSignals(False)
        self._ratio_src = "d" if target == "d" else "c"
        self._ratio_calc()
        self._res_tip_warn = False
        self._refresh_res_tip()
        label = "D" if target == "d" else "C"
        self.status.setText(f"✔ 已写入 {label} = {int(value)}")

    def _apply_c_value(self, value: int):
        """兼容旧调用：若未选中则选 C 再写。"""
        if getattr(self, "_cd_target", None) not in ("c", "d"):
            self._set_cd_target("c")
        self._apply_res_value(value)


    # ──────── 简易计算器 ────────
    def _calc_eval(self):
        expr = (self.calc_expr.text() or "").strip()
        try:
            res = self._safe_eval(expr)
            self.calc_result.setText(str(res))
            self.status.setText("🟢 计算器完成")
        except Exception:
            self.calc_result.setText("表达式错误")
            self.status.setText("❌ 表达式错误")

    def _safe_eval(self, expr: str):
        if not expr: return ""
        node = ast.parse(expr, mode="eval")
        def _eval(n):
            if isinstance(n, ast.Expression): return _eval(n.body)
            if isinstance(n, ast.Num): return n.n
            if hasattr(ast, "Constant") and isinstance(n, ast.Constant) and isinstance(n.value, (int, float)): return n.value
            if isinstance(n, ast.BinOp):
                l, r = _eval(n.left), _eval(n.right)
                if isinstance(n.op, ast.Add): return l + r
                if isinstance(n.op, ast.Sub): return l - r
                if isinstance(n.op, ast.Mult): return l * r
                if isinstance(n.op, ast.Div): return l / r
                if isinstance(n.op, ast.FloorDiv): return l // r
                if isinstance(n.op, ast.Mod): return l % r
                if isinstance(n.op, ast.Pow): return l ** r
                raise ValueError("不支持的运算符")
            if isinstance(n, ast.UnaryOp):
                v = _eval(n.operand)
                if isinstance(n.op, ast.UAdd): return +v
                if isinstance(n.op, ast.USub): return -v
                raise ValueError("不支持的前缀运算")
            raise ValueError("不支持的表达式")
        return _eval(node)

    # ──────── 金额大小写转换 ────────
    def _convert_amount(self):
        txt = self.amount_input.text().strip()
        try:
            result = self._num_to_rmb(txt)
            self.amount_output.setText(result)
            self.status.setText("🟢 转换完成")
        except Exception:
            self.amount_output.setText("输入有误")
            self.status.setText("❌ 请输入有效数字金额")

    def _num_to_rmb(self, num_str: str) -> str:
        """数字字符串 → 人民币大写（支持小数最多两位、负数）"""
        DIGITS   = "零壹贰叁肆伍陆柒捌玖"
        UNITS    = ["", "拾", "佰", "仟"]
        SECTIONS = ["", "万", "亿"]

        negative = num_str.startswith("-")
        num_str  = num_str.lstrip("-")

        if "." in num_str:
            int_str, dec_str = num_str.split(".", 1)
            dec_str = (dec_str + "00")[:2]
        else:
            int_str, dec_str = num_str, "00"

        int_val = int(int_str) if int_str else 0
        jiao    = int(dec_str[0])
        fen     = int(dec_str[1])

        def _group4(n: int) -> str:
            if n == 0: return ""
            digits = []
            for _ in range(4):
                digits.append(n % 10); n //= 10
            digits.reverse()
            res = ""; need_zero = False
            for i, d in enumerate(digits):
                if d == 0:
                    need_zero = True
                else:
                    if need_zero: res += "零"
                    res += DIGITS[d] + UNITS[3 - i]
                    need_zero = False
            return res

        if int_val == 0:
            int_chinese = "零"
        else:
            groups = []
            tmp = int_val
            while tmp > 0:
                groups.append(tmp % 10000); tmp //= 10000
            groups.reverse()
            parts = []
            prev_zero = False
            for i, g in enumerate(groups):
                if g == 0:
                    prev_zero = True
                else:
                    gc = _group4(g)
                    if prev_zero and parts: gc = "零" + gc
                    parts.append(gc + SECTIONS[len(groups) - 1 - i])
                    prev_zero = False
            int_chinese = "".join(parts)

        result = ("负" if negative else "") + int_chinese + "元"
        if jiao == 0 and fen == 0:
            result += "整"
        else:
            if jiao > 0: result += DIGITS[jiao] + "角"
            if fen  > 0: result += DIGITS[fen]  + "分"
        return result

    def _copy_amount(self):
        val = self.amount_output.text()
        if val and val != "输入有误":
            from PyQt5.QtCore import QMimeData
            from PyQt5.QtWidgets import QApplication
            m = QMimeData(); m.setText(val); QApplication.clipboard().setMimeData(m)
            self.status.setText("🟢 已复制大写金额到剪贴板")

    # ──────── 大写汉字速查 ────────
    def _copy_char(self, char: str):
        from PyQt5.QtCore import QMimeData
        from PyQt5.QtWidgets import QApplication
        m = QMimeData(); m.setText(char); QApplication.clipboard().setMimeData(m)
        self._char_status.setText(f"✅ 已复制「{char}」")
