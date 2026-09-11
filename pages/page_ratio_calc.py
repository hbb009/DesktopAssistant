# pages/page_ratio_calc.py
# 比例计算（实用工具 · 子页）：无内层套框，分区用「空隙 + 实线 + 空隙」分隔

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFrame, QButtonGroup, QScrollArea, QSizePolicy,
)
from PyQt5.QtCore import Qt, QTimer
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

# 分隔线粗细（px）
_SEP_PX = 3


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
        ratio_box.setContentsMargins(0, 0, 0, CARD_BOTTOM_GAP)
        ratio_box.setSpacing(CARD_TITLE_BODY_GAP)
        self._theme_titles.append(install_card_title(sec_ratio, ratio_box, "比例换算"))
        left.addWidget(sec_ratio)

        self.a, self.b, self.c, self.d = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        for w, p, obj in [
            (self.a, 'A', 'RatioA'), (self.b, 'B', 'RatioB'),
            (self.c, 'C', 'RatioC'), (self.d, 'D', 'RatioD'),
        ]:
            w.setPlaceholderText(p); w.setObjectName(obj)
            w.setFixedHeight(40); w.setAlignment(Qt.AlignCenter)
            # 只加字号，不整段 setStyleSheet 冲掉全局 QLineEdit 底色/描边
            f = w.font(); f.setPixelSize(16); w.setFont(f)
        self.d.setReadOnly(True); self.d.setEnabled(False)

        self._debounce = QTimer(self); self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._ratio_calc)
        for w in (self.a, self.b, self.c):
            w.textChanged.connect(lambda _=None: self._debounce.start(600))

        # 输入区：左 A/B + 交换（贴 A、B 右侧垂直居中）｜ 右 C/D + 复制
        inputs_row = QHBoxLayout()
        inputs_row.setSpacing(8)
        ratio_box.addLayout(inputs_row)

        # ── A / B 列 ──
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

        # 「交换」贴在 A、B 两框右侧正中间
        btn_swap = QPushButton("交换")
        btn_swap.setProperty("role", "nav")
        btn_swap.style().unpolish(btn_swap); btn_swap.style().polish(btn_swap)
        btn_swap.clicked.connect(self._swap)
        inputs_row.addWidget(btn_swap, 0, Qt.AlignVCenter)

        inputs_row.addStretch(1)

        # ── C / D 列 ──
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

        # 输入区与预设芯片：同区内也用实线轻分
        ratio_box.addSpacing(8)
        h_in = _make_h_sep()
        self._h_seps.append(h_in)
        ratio_box.addWidget(h_in)
        ratio_box.addSpacing(8)

        chips_row = FlowLayout(h_spacing=8, v_spacing=8)
        ratio_box.addLayout(chips_row)
        presets = [
            ("1:1",   1,  1, 1536), ("16:9", 16,  9, 1536),
            ("4:3",   4,  3, 1536), ("3:2",   3,  2, 1536),
            ("3:4",   3,  4, 1536), ("2:3",   2,  3, 1536),
            ("9:16",  9, 16, 1536), ("21:9", 21,  9, 2048),
        ]
        self._chip_group = QButtonGroup(self); self._chip_group.setExclusive(True)
        for name, ra, rb, long_side in presets:
            short = round(long_side * rb / ra) if ra >= rb else round(long_side * ra / rb)
            w_px  = long_side if ra >= rb else short
            h_px  = short     if ra >= rb else long_side
            b = QPushButton(name); b.setCheckable(True); b.setObjectName("RatioChip")
            b.setProperty("role", "nav")
            b.setToolTip(f"{name}（{w_px}×{h_px}）")
            b.style().unpolish(b); b.style().polish(b)
            b.clicked.connect(lambda _, a=ra, bb=rb, label=name, c=long_side:
                              self._apply_ratio(a, bb, label, c))
            self._chip_group.addButton(b); chips_row.addWidget(b)

        # 空隙 + 实线 + 空隙
        left.addSpacing(10)
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

        # ── 简易计算 ──────────────────────────────────────
        sec_calc = make_card("CardRatioSimple", borderless=True)
        calc_box = QVBoxLayout(sec_calc)
        calc_box.setContentsMargins(0, 0, 0, CARD_BOTTOM_GAP)
        calc_box.setSpacing(CARD_TITLE_BODY_GAP)
        self._theme_titles.append(install_card_title(sec_calc, calc_box, "简易计算"))
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

        # 首刷分隔线色 + 订阅主题
        self._restyle_seps()
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

    # ──────── 比例计算 ────────
    def _ratio_calc(self):
        try:
            a = float(self.a.text()); b = float(self.b.text()); c = float(self.c.text())
            if a == 0: raise ZeroDivisionError
            d = b * c / a
            self.d.setText(f"{d:.2f}")
            self.status.setText("🟢 计算完成")
        except Exception:
            self.d.clear()
            self.status.setText("⚪ 等待输入…")

    def _swap(self):
        a_txt, b_txt = self.a.text(), self.b.text()
        self.a.blockSignals(True); self.b.blockSignals(True)
        self.a.setText(b_txt); self.b.setText(a_txt)
        self.a.blockSignals(False); self.b.blockSignals(False)
        self._ratio_calc(); self.a.setFocus()

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
        self._ratio_calc()
        if c:
            short = round(c * rb / ra) if ra >= rb else round(c * ra / rb)
            w_px  = c     if ra >= rb else short
            h_px  = short if ra >= rb else c
            self.status.setText(f"🟢 {name}  →  {w_px}×{h_px} px（已自动计算）")
        else:
            self.status.setText(f"🟡 已选择比例 {name}，请在 C 输入具体数值")

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
