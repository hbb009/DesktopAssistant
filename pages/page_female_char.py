# pages/page_female_char.py
# GPT Image 角色母模板 v2：模块直接铺开，无分组标题。
# 固定：体型 / 皮肤规则 / 镜头 / 渲染。替换：身份 / 外貌 / 服装 / 装备 / 姿势 / 场景 / 色彩。

from __future__ import annotations

from typing import Dict, List, Tuple

from PyQt5.QtCore import Qt, QTimer, QSize, QRectF
from PyQt5.QtGui import (
    QColor, QPalette, QIcon, QPixmap, QPainter, QPen, QTextCharFormat,
    QSyntaxHighlighter,
)
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit,
    QScrollArea, QFrame, QSizePolicy, QApplication, QButtonGroup, QDialog,
    QDialogButtonBox, QFormLayout, QLineEdit, QMenu,
)

from styles.style_all import theme, tk, apply_mini_button
from utils.flow_layout import FlowLayout
from utils.logger import get_logger
from pages.page_prompt_editor import _GrowEdit

log = get_logger(__name__)

_EDIT_PAD_V = 6
_EDIT_PAD_H = 8
_TITLE_FONT = 16
_TITLE_PAD_V = 5
_COPY_H = 33
_LOCK_ICON = 14
_USER_BLUE_DARK = "#93c5fd"
_USER_BLUE_LIGHT = "#1d4ed8"
_SPLIT_GAP = 6

# 可替换模块：key, 标题, 占位, 组装用 [TOKEN]
_FIELDS: List[Tuple[str, str, str, str]] = [
    ("identity", "角色身份", "a female emergency room nurse（不要写体型）", "[CHARACTER IDENTITY AND PROFESSION]"),
    ("face", "外貌", "an East Asian woman with long black hair tied into a practical ponytail", "[FACE, ETHNICITY AND HAIR DESCRIPTION]"),
    ("expression", "表情", "calm focused expression", "[FACIAL EXPRESSION AND EMOTIONAL STATE]"),
    ("outfit", "职业服装", "颜色、材料、功能、结构、剪裁；不要只写 bodysuit", "[DETAILED PROFESSION-APPROPRIATE OUTFIT]"),
    ("accessories", "装备", "职业相关装备，如 a compact medical scanner", "[PROFESSIONAL EQUIPMENT OR ACCESSORIES]"),
    ("pose", "姿势", "standing in a natural three-quarter stance, one powerful leg slightly forward", "[BODY POSE AND ACTION]"),
    ("scene", "主场景", "Inside a futuristic emergency hospital at night.", "[PRIMARY ENVIRONMENT AND LOCATION]"),
    ("scene_detail", "场景细节", "Advanced medical holograms, autonomous surgical systems...", "[SECONDARY ENVIRONMENT DETAILS AND STORY ELEMENTS]"),
    ("palette", "色彩", "cyan, magenta, crimson, electric blue and violet", "[COLOR PALETTE]"),
]

# 分区顺序（不再有「01 角色身份」等分组标题，直接按序铺开）
_SECTION_ORDER: List[str] = [
    "identity", "body", "face", "expression", "outfit", "accessories",
    "camera", "pose", "scene", "scene_detail", "palette",
]

_BODY_DEFAULT = (
    "with an exceptionally powerful feminine elite-athlete physique: extremely thick, "
    "heavily muscular thighs with massive quadriceps and powerful hamstrings, "
    "substantial lower-body muscle volume clearly beyond that of an ordinary athletic woman, "
    "realistic natural muscle definition, strong athletic leg proportions, "
    "a dramatically narrow waist creating a pronounced contrast with the powerful lower body, "
    "an elegant feminine upper-body silhouette, and an extremely large, full, heavy "
    "natural-looking bust, approximately twice the volume of a typical curvy figure, "
    "fully covered by clothing with no deliberate cleavage exposure"
)

_CAMERA_DEFAULT = (
    "Cowboy shot, thigh-up composition, eye-level camera, "
    "the character occupying approximately 70–80% of the frame"
)

_SKIN_FIXED = (
    "Smooth, healthy, realistic skin, no exaggerated vascularity, no prominent veins, "
    "natural skin texture and realistic anatomical proportions."
)

_RENDER_TAIL = (
    "Cinematic lighting appropriate to the environment, realistic skin pores, "
    "individual hair strands, highly detailed fabric and material textures, "
    "physically based rendering, natural reflections, atmospheric depth, "
    "volumetric lighting, HDR, ray tracing, 85mm lens, shallow depth of field, "
    "ultra photorealistic, cinematic feature-film quality, realistic human anatomy, "
    "high detail, rich saturated colors, 8K."
)

# 出厂预设：(显示名, 写入值)
_PRESETS: Dict[str, List[Tuple[str, str]]] = {
    "identity": [
        ("急诊护士", "a female emergency room nurse"),
        ("刺客", "a female assassin"),
        ("奥术法师", "a female arcane sorceress"),
        ("网球教练", "a female professional tennis coach"),
        ("航天工程师", "a female aerospace engineer"),
        ("厨师", "a female chef"),
        ("建筑师", "a female architect"),
        ("野生摄影师", "a female wildlife photographer"),
        ("指挥家", "a female orchestra conductor"),
        ("航天机械师", "a female aerospace mechanic"),
        ("警探", "a female police detective"),
        ("科学家", "a female scientist"),
        ("兽医", "a female veterinarian"),
        ("空姐", "a female flight attendant"),
        ("舞蹈教练", "a female dance instructor"),
    ],
    "face": [
        ("东亚马尾", "an East Asian woman with long black hair tied into a practical ponytail"),
        ("东亚短发", "an East Asian woman with a sharp short bob and dark almond eyes"),
        ("栗棕中发", "a mixed-heritage woman with shoulder-length chestnut hair and warm brown eyes"),
        ("铂金短发", "a pale-skinned woman with a short platinum pixie cut and cool grey eyes"),
        ("银白长发", "a woman with long silver-white hair, pale blue eyes and defined cheekbones"),
    ],
    "expression": [
        ("冷静专注", "a calm, focused expression"),
        ("自信浅笑", "a confident slight smile"),
        ("坚定凝视", "a serious, determined gaze"),
        ("柔和克制", "a soft, composed expression"),
        ("高度专注", "an intense look of concentration"),
    ],
    "outfit": [
        ("急诊护士装", "a premium futuristic emergency medical uniform, combining a fitted cobalt-blue medical bodysuit with white structured panels, practical reinforced seams and subtle medical equipment attachments"),
        ("刺客战术装", "a premium matte-black tactical infiltration suit with deep-crimson structural seams, flexible armored panels and silent-step construction"),
        ("奥术法师装", "a luxurious deep-violet and midnight-blue arcane robe-bodysuit hybrid made from velvet-like technical fabric, with silver rune embroidery, a structured high collar and layered ceremonial panels"),
        ("网球教练装", "a performance-cut athletic coaching uniform in white and court-green technical knit, with a fitted structured jacket, high-stretch panels and practical training pockets"),
        ("航天工程装", "a high-tech aerospace workshop uniform combining a fitted charcoal-and-orange technical bodysuit with heat-resistant panels, reinforced seams and magnetic-tool attachment points"),
        ("厨师装", "a premium charcoal-and-ivory culinary uniform with a fitted double-breasted jacket, heat-resistant technical fabric, structured silhouette and reinforced cuffs"),
        ("建筑师装", "a contemporary architectural field uniform in ivory and graphite, combining a tailored technical coat with a fitted high-neck layer, document pockets and weather-resistant panels"),
        ("摄影师装", "a rugged outdoor photography kit: a fitted olive-and-charcoal weatherproof jacket over a structured high-neck layer, with harness points and stretch panels"),
        ("指挥家装", "a formal concert-black conducting ensemble with a structured high-neck blouse, a tailored long coat and fluid technical fabric"),
        ("航天维修装", "a heavy-duty space-station maintenance suit in cobalt and hazard-orange, with heat-resistant plates, tool loops and a fitted waist over powerful thighs"),
    ],
    "accessories": [
        ("医疗扫描仪", "a compact medical scanner and professional identification equipment"),
        ("消音枪套", "a compact holster, suppressed sidearm and night-vision optics"),
        ("法杖符咒", "a spell-focus staff and floating rune charms"),
        ("球拍与平板", "a racket bag and a digital training tablet"),
        ("工程护目镜", "a diagnostic visor and a portable engineering scanner"),
        ("厨刀", "professional knives in a compact holster and a precision thermometer"),
        ("全息图纸", "a holographic blueprint tablet and a laser measuring device"),
        ("长焦相机", "a professional camera with a long lens and a compact drone controller"),
        ("指挥棒", "a conducting baton and a discreet in-ear monitor"),
        ("磁力工具", "a multi-tool gauntlet and a magnetic parts pouch"),
    ],
    "pose": [
        ("双腿均衡", "standing confidently with her weight naturally balanced on both legs"),
        ("四分之三站姿", "standing in a relaxed but powerful three-quarter stance, one leg slightly forward"),
        ("强调大腿", "standing in a natural three-quarter stance, one powerful leg slightly forward, clearly emphasizing the massive muscular thigh structure"),
        ("一手叉腰", "standing with one hand resting on her hip, weight shifted onto the forward leg"),
        ("回身过肩", "looking back over her shoulder in a controlled three-quarter turn"),
    ],
    "scene": [
        ("急诊医院", "Inside a futuristic emergency hospital at night."),
        ("古代神殿", "Inside an enormous ancient magical temple."),
        ("雨夜屋顶", "On a rain-soaked rooftop above a neon city."),
        ("训练中心", "Inside a high-performance indoor training complex."),
        ("高级厨房", "Inside a Michelin-star futuristic kitchen."),
        ("未来工地", "On a luminous architectural construction site in a future city."),
        ("野外", "In a golden-hour wilderness photography reserve."),
        ("音乐厅", "Inside a grand concert hall before the performance."),
        ("太空维修站", "Inside a gigantic spacecraft maintenance hangar."),
    ],
    "scene_detail": [
        ("急诊夜景", "Advanced medical holograms, autonomous surgical systems, glowing medical monitors and panoramic windows overlooking a neon city."),
        ("神殿能量", "Floating crystals, glowing arcane symbols and mysterious energy structures."),
        ("屋顶暗口", "Distant drones, reflective puddles and a shadowed underground access hatch."),
        ("训练场", "Weighted racks, motion-capture cameras and a glass wall overlooking the court."),
        ("未来厨房", "Precision cooktops, hovering recipe holograms and a polished steel pass."),
        ("工地图纸", "Holographic blueprints, crane lights and unfinished mega-structures."),
        ("山雾营地", "Mist, dramatic mountains and a remote equipment camp."),
        ("乐池候场", "Warm stage lights, empty velvet seats and a waiting orchestra."),
        ("机库核心", "Robotic arms, glowing energy cores and floating diagnostic screens."),
    ],
    "palette": [
        ("赛博朋克", "cyan, magenta, crimson, electric blue and violet"),
        ("魔法奇幻", "violet, sapphire blue, emerald green, gold and magenta"),
        ("高级职业", "navy blue, gold, ivory, emerald green and warm amber"),
        ("科幻工业", "cobalt blue, orange, crimson, cyan and metallic silver"),
        ("自然环境", "emerald green, turquoise, golden orange, crimson and deep blue"),
    ],
    "body": [
        ("完整体型", _BODY_DEFAULT),
        ("粗腿+肌群", "extremely thick, heavily muscular thighs with massive quadriceps and powerful hamstrings"),
        ("体积对比", "substantial lower-body muscle volume clearly beyond that of an ordinary athletic woman"),
        ("细腰反差", "a dramatically narrow waist creating a pronounced contrast with the powerful lower body"),
        ("重量感胸部", "an extremely large, full, heavy natural-looking bust, approximately twice the volume of a typical curvy figure, fully covered by clothing with no deliberate cleavage exposure"),
    ],
}


def _strip_span(cur: str, value: str) -> str:
    """从正文里拿掉一段模块；顺带清掉多余逗号。"""
    cur = (cur or "").strip()
    value = (value or "").strip()
    if not cur or not value:
        return cur
    if cur == value:
        return ""
    idx = cur.find(value)
    if idx < 0:
        return cur
    before = cur[:idx].rstrip()
    after = cur[idx + len(value):].lstrip()
    if before.endswith(","):
        before = before[:-1].rstrip()
    if after.startswith(","):
        after = after[1:].lstrip()
    if before and after:
        return before + ", " + after
    return before or after


def _lock_icon(locked: bool, size: int, color: str) -> QIcon:
    """闭锁 / 开锁线框，给体型锁定按钮用。"""
    size = max(12, int(size))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    s = float(size)
    c = QColor(color)
    pen = QPen(c, max(1.5, s * 0.11))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(s * 0.20, s * 0.48, s * 0.60, s * 0.40), s * 0.10, s * 0.10)
    if locked:
        p.drawArc(QRectF(s * 0.30, s * 0.16, s * 0.40, s * 0.46), 0, 180 * 16)
    else:
        p.drawArc(QRectF(s * 0.40, s * 0.10, s * 0.42, s * 0.50), -10 * 16, 190 * 16)
    p.end()
    return QIcon(pm)


class _PromptBuf:
    """按片段拼接提示词，并记下用户填写段的字符区间。"""

    def __init__(self):
        self._parts: List[str] = []
        self.spans: List[Tuple[int, int]] = []

    def add(self, text: str, user: bool = False):
        if not text:
            return
        start = sum(len(p) for p in self._parts)
        self._parts.append(text)
        if user:
            self.spans.append((start, start + len(text)))

    def text(self) -> str:
        return "".join(self._parts)


def _add_slot(buf: _PromptBuf, raw: str, token: str):
    s = (raw or "").strip()
    if s:
        buf.add(s, user=True)
    else:
        buf.add(token, user=False)


def _as_identity(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    low = s.lower()
    if low.startswith(("a female ", "an female ", "a woman ", "an east ", "a mixed")):
        return s
    if low.startswith("female "):
        return "a " + s
    return "a female " + s


def _is_legacy_body(text: str) -> bool:
    t = " ".join((text or "").split())
    if not t:
        return False
    if "heavily muscular thighs with massive quadriceps" in t:
        return False
    return "extremely thick heavily muscular thighs" in t


def _migrate_saved_fields(fields: Dict[str, str], body: str) -> Tuple[Dict[str, str], str]:
    """旧版 11 栏（职业词 / 主色辅色 / bodysuit）迁到 v2 七组。"""
    src = dict(fields or {})
    out: Dict[str, str] = {}
    for key, _t, _p, _tok in _FIELDS:
        out[key] = str(src.get(key) or "").strip()

    if not out["identity"]:
        old = str(src.get("profession") or "").strip()
        if old:
            out["identity"] = _as_identity(old)

    outfit = out["outfit"]
    primary = str(src.get("primary") or "").strip()
    secondary = str(src.get("secondary") or "").strip()
    material = str(src.get("material") or "").strip()
    if outfit and len(outfit.split()) <= 4 and (primary or secondary or material):
        color = " and ".join(x for x in (primary, secondary) if x)
        mat = f" made from {material}" if material else ""
        color_bit = f"{color} " if color else ""
        out["outfit"] = f"a premium {color_bit}{outfit} uniform{mat}".strip()

    body_text = (body or "").strip()
    if not body_text or _is_legacy_body(body_text):
        body_text = _BODY_DEFAULT
    return out, body_text


def compose_prompt_ex(fields: Dict[str, str], body: str) -> Tuple[str, List[Tuple[int, int]]]:
    buf = _PromptBuf()
    buf.add("A hyper-realistic adult woman, ")
    _add_slot(buf, fields.get("identity", ""), "[CHARACTER IDENTITY AND PROFESSION]")
    buf.add(", ")
    _add_slot(buf, fields.get("face", ""), "[FACE, ETHNICITY AND HAIR DESCRIPTION]")
    buf.add(", ")
    body_text = (body or "").strip() or _BODY_DEFAULT
    if not body_text.lower().startswith("with "):
        buf.add("with ")
    buf.add(body_text, user=(body_text != _BODY_DEFAULT))
    buf.add(".\n\n")
    buf.add(_SKIN_FIXED + " ")
    _add_slot(buf, fields.get("expression", ""), "[FACIAL EXPRESSION AND EMOTIONAL STATE]")
    buf.add(".\n\nShe is wearing ")
    _add_slot(buf, fields.get("outfit", ""), "[DETAILED PROFESSION-APPROPRIATE OUTFIT]")
    buf.add(
        ", designed specifically for her profession and environment, with a "
        "structured silhouette, high or moderate neckline, no exposed midriff, "
        "naturally fitted around the dramatically narrow waist and exceptionally "
        "powerful muscular thighs, realistic fabric compression, folds and tension, "
    )
    _add_slot(buf, fields.get("accessories", ""), "[PROFESSIONAL EQUIPMENT OR ACCESSORIES]")
    buf.add(".\n\n")
    buf.add(_CAMERA_DEFAULT)
    buf.add(", ")
    _add_slot(buf, fields.get("pose", ""), "[BODY POSE AND ACTION]")
    buf.add(".\n\n")
    _add_slot(buf, fields.get("scene", ""), "[PRIMARY ENVIRONMENT AND LOCATION]")
    buf.add(" ")
    _add_slot(
        buf,
        fields.get("scene_detail", ""),
        "[SECONDARY ENVIRONMENT DETAILS AND STORY ELEMENTS]",
    )
    buf.add("\n\nRich, vibrant and carefully controlled color palette featuring ")
    _add_slot(buf, fields.get("palette", ""), "[COLOR PALETTE]")
    buf.add(". " + _RENDER_TAIL)
    return buf.text(), buf.spans


def compose_prompt(fields: Dict[str, str], body: str) -> str:
    text, _spans = compose_prompt_ex(fields, body)
    return text


class _UserSpanHighlighter(QSyntaxHighlighter):
    """把用户填写的片段刷成蓝色。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._spans: List[Tuple[int, int]] = []
        self._color = QColor(_USER_BLUE_LIGHT)

    def set_spans(self, spans: List[Tuple[int, int]], color: QColor):
        self._spans = list(spans or [])
        self._color = QColor(color)
        self.rehighlight()

    def highlightBlock(self, text: str):
        block = self.currentBlock()
        start = block.position()
        end = start + len(text)
        fmt = QTextCharFormat()
        fmt.setForeground(self._color)
        for a, b in self._spans:
            s = max(int(a), start)
            e = min(int(b), end)
            if e > s:
                self.setFormat(s - start, e - s, fmt)


def _edit_colors(kind: str) -> dict:
    """输入=绿（新稿），预览=蓝，锁定体型=灰。"""
    dark = theme.is_dark
    if kind == "lock":
        if dark:
            return {
                "border": "#64748b",
                "empty": "#10141c",
                "filled": "#1a2230",
                "placeholder": "#334155",
            }
        return {
            "border": "#94a3b8",
            "empty": "#c5ced8",
            "filled": "#eef2f6",
            "placeholder": "#94a3b8",
        }
    if kind == "blue":
        if dark:
            return {
                "border": "#3b82f6",
                "empty": "#071018",
                "filled": "#16324f",
                "placeholder": "#1a2634",
            }
        return {
            "border": "#3b82f6",
            "empty": "#9bb0cc",
            "filled": "#f4f8ff",
            "placeholder": "#87a0b8",
        }
    if dark:
        return {
            "border": "#2f8f55",
            "empty": "#07140e",
            "filled": "#163528",
            "placeholder": "#1c2c24",
        }
    return {
        "border": "#22c55e",
        "empty": "#9ec3a8",
        "filled": "#f4fdf7",
        "placeholder": "#87a894",
    }


class _SplitRow(QWidget):
    """左右等宽：左编辑、右预设。"""

    def __init__(self, spacing: int = _SPLIT_GAP, parent=None):
        super().__init__(parent)
        self._spacing = int(spacing)
        self._left = None
        self._right = None
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(self._spacing)

    def set_columns(self, left: QWidget, right: QWidget):
        self._left = left
        self._right = right
        for w in (left, right):
            w.setMinimumWidth(0)
            w.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Maximum)
        self._lay.setAlignment(Qt.AlignTop)
        self._lay.addWidget(left, 0, Qt.AlignTop)
        self._lay.addWidget(right, 0, Qt.AlignTop)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._left is None or self._right is None:
            return
        half = max(1, (self.width() - self._spacing) // 2)
        if self._left.width() != half:
            self._left.setFixedWidth(half)
        if self._right.width() != half:
            self._right.setFixedWidth(half)


class _AddModuleDialog(QDialog):
    """名称 + 英文内容。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加模块")
        self.setModal(True)
        self.setMinimumWidth(420)
        self._name = QLineEdit()
        self._name.setPlaceholderText("显示名称，如 空姐")
        self._value = QPlainTextEdit()
        self._value.setPlaceholderText("点选后写入左侧的英文内容")
        self._value.setFixedHeight(96)
        form = QFormLayout()
        form.addRow("名称", self._name)
        form.addRow("内容", self._value)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(box)

    def pair(self) -> Tuple[str, str]:
        return (
            (self._name.text() or "").strip(),
            (self._value.toPlainText() or "").strip(),
        )


class PageFemaleChar(QWidget):
    """女性职业角色模块化母模板。"""

    def __init__(self):
        super().__init__()
        self.setObjectName("PageRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._loading = False
        self._prefs_dirty_cb = None
        self._edits: Dict[str, QPlainTextEdit] = {}
        self._title_bars: List[QFrame] = []
        self._labels: List[QLabel] = []
        self._chip_groups: Dict[str, QButtonGroup] = {}
        self._chip_btns: List[QPushButton] = []
        self._add_btns: List[QPushButton] = []
        self._preset_flows: Dict[str, FlowLayout] = {}
        self._preset_panes: Dict[str, QFrame] = {}
        self._custom: Dict[str, List[dict]] = {}
        self._body_locked = True

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._persist_now)

        scroll = QScrollArea(self)
        scroll.setObjectName("FemaleScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setAttribute(Qt.WA_StyledBackground, True)
        scroll.setStyleSheet(
            "QScrollArea#FemaleScroll{background:transparent;border:none;}"
            "QScrollArea#FemaleScroll > QWidget > QWidget{background:transparent;}"
        )
        try:
            scroll.viewport().setAutoFillBackground(False)
            scroll.viewport().setStyleSheet("background:transparent;")
        except Exception:
            pass

        host = QWidget()
        host.setObjectName("FemaleScrollHost")
        host.setAttribute(Qt.WA_StyledBackground, True)
        host.setStyleSheet("#FemaleScrollHost{background:transparent;border:none;}")
        scroll.setWidget(host)

        root = QVBoxLayout(host)
        root.setContentsMargins(0, 0, 0, 8)
        root.setSpacing(10)

        hint = QLabel(
            "各栏预设可叠加，点选即加入左侧编辑区；再点一次同一预设即可取消该段。自定义模块可右键删除。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("FemaleHint")
        self._hint = hint
        self._style_hint(hint)
        root.addWidget(hint)

        spec = {k: (t, p) for k, t, p, _tok in _FIELDS}
        for key in _SECTION_ORDER:
            if key == "body":
                root.addWidget(self._make_body_section())
            elif key == "camera":
                root.addWidget(self._make_locked_block(
                    "camera", "镜头构图（固定）", _CAMERA_DEFAULT
                ))
            else:
                title, placeholder = spec[key]
                root.addWidget(self._make_section(key, title, placeholder, "green"))

        root.addWidget(self._make_preview_section())

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        foot.setSpacing(6)
        self.btn_copy = self._make_foot_btn("复制提示词", "把组装好的英文提示词复制到剪贴板")
        self.btn_copy.clicked.connect(self._copy_prompt)
        foot.addWidget(self.btn_copy, 1)
        foot_wrap = QWidget()
        foot_wrap.setObjectName("FemaleFoot")
        foot_wrap.setAttribute(Qt.WA_StyledBackground, True)
        foot_wrap.setStyleSheet("#FemaleFoot{background:transparent;border:none;}")
        foot_wrap.setLayout(foot)
        self._scroll = scroll
        self._foot_wrap = foot_wrap

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        outer.addWidget(scroll, 1)
        outer.addWidget(foot_wrap, 0)

        try:
            scroll.verticalScrollBar().rangeChanged.connect(self._sync_foot_align)
        except Exception:
            pass
        QTimer.singleShot(0, self._sync_foot_align)
        QTimer.singleShot(0, self._refresh_preview)

        try:
            theme.changed.connect(self.restyle_theme)
        except Exception:
            pass

    def _make_locked_block(self, key: str, title: str, text: str) -> QWidget:
        wrap = QWidget()
        wrap.setObjectName(f"FemaleSection_{key}")
        wrap.setAttribute(Qt.WA_StyledBackground, True)
        wrap.setStyleSheet("background:transparent;border:none;")
        col = QVBoxLayout(wrap)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        bar, lab = self._make_field_bar(title)
        self._title_bars.append(bar)
        self._labels.append(lab)
        col.addWidget(bar)
        edit = self._make_edit("lock", key, "")
        edit.setPlainText(text)
        edit.setReadOnly(True)
        self._edits[key] = edit
        col.addWidget(edit)
        return wrap

    def _make_section(self, key: str, title: str, placeholder: str, tint: str) -> QWidget:
        wrap = QWidget()
        wrap.setObjectName(f"FemaleSection_{key}")
        wrap.setAttribute(Qt.WA_StyledBackground, True)
        wrap.setStyleSheet("background:transparent;border:none;")
        col = QVBoxLayout(wrap)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        bar, lab = self._make_field_bar(title)
        self._title_bars.append(bar)
        self._labels.append(lab)
        col.addWidget(bar)
        edit = self._make_edit(tint, key, placeholder)
        self._edits[key] = edit
        pane = self._make_preset_pane(key, tint)
        split = _SplitRow(_SPLIT_GAP, wrap)
        split.set_columns(edit, pane)
        col.addWidget(split)
        return wrap

    def _make_body_section(self) -> QWidget:
        wrap = QWidget()
        wrap.setObjectName("FemaleSection_body")
        wrap.setAttribute(Qt.WA_StyledBackground, True)
        wrap.setStyleSheet("background:transparent;border:none;")
        col = QVBoxLayout(wrap)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)

        bar = QFrame()
        bar.setObjectName("PromptFieldBar")
        bar.setFrameShape(QFrame.NoFrame)
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setAutoFillBackground(True)
        bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(8, _TITLE_PAD_V, 8, _TITLE_PAD_V)
        bar_lay.setSpacing(8)
        lab = QLabel("体型（建议锁定）")
        lab.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        lab.setAttribute(Qt.WA_StyledBackground, True)
        self.btn_unlock = apply_mini_button(QPushButton("解锁"))
        self.btn_unlock.setToolTip("解锁后可改体型段；一般不建议动")
        self.btn_unlock.setFocusPolicy(Qt.NoFocus)
        self.btn_unlock.setFixedHeight(24)
        self.btn_unlock.setIconSize(QSize(_LOCK_ICON, _LOCK_ICON))
        self.btn_unlock.clicked.connect(self._toggle_body_lock)
        bar_lay.addWidget(lab, 1)
        bar_lay.addWidget(self.btn_unlock, 0)
        self._title_bars.append(bar)
        self._labels.append(lab)
        self._body_title_lab = lab
        self._style_field_bar(bar, lab)
        self._style_unlock_btn()
        col.addWidget(bar)

        note = QLabel("递进：粗 → 肌肉 → 肌群位置 → 总体体积 → 与普通运动员对比。细腰反差、胸部重量感。不要写腹肌、不要露腹。")
        note.setWordWrap(True)
        note.setObjectName("FemaleBodyNote")
        self._body_note = note
        self._style_hint(note)
        col.addWidget(note)

        edit = self._make_edit("lock", "body", "体型控制段")
        edit.setPlainText(_BODY_DEFAULT)
        edit.setReadOnly(True)
        self._edits["body"] = edit
        pane = self._make_preset_pane("body", "lock")
        split = _SplitRow(_SPLIT_GAP, wrap)
        split.set_columns(edit, pane)
        col.addWidget(split)
        return wrap

    def _make_preview_section(self) -> QWidget:
        wrap = QWidget()
        wrap.setObjectName("FemaleSection_preview")
        wrap.setAttribute(Qt.WA_StyledBackground, True)
        wrap.setStyleSheet("background:transparent;border:none;")
        col = QVBoxLayout(wrap)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        bar, lab = self._make_field_bar("完整提示词")
        self._title_bars.append(bar)
        self._labels.append(lab)
        col.addWidget(bar)
        edit = self._make_edit("blue", "preview", "组装结果会显示在这里")
        edit.setReadOnly(True)
        self._preview = edit
        self._preview_hl = _UserSpanHighlighter(edit.document())
        col.addWidget(edit)
        return wrap

    def _make_field_bar(self, title: str):
        bar = QFrame()
        bar.setObjectName("PromptFieldBar")
        bar.setFrameShape(QFrame.NoFrame)
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setAutoFillBackground(True)
        bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(0, _TITLE_PAD_V, 0, _TITLE_PAD_V)
        bar_lay.setSpacing(0)
        lab = QLabel(title)
        lab.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        lab.setAttribute(Qt.WA_StyledBackground, True)
        bar_lay.addWidget(lab)
        self._style_field_bar(bar, lab)
        return bar, lab

    def _make_edit(self, tint: str, key: str, placeholder: str) -> _GrowEdit:
        edit = _GrowEdit()
        edit.setObjectName(f"FemaleFieldEdit_{tint}_{key}")
        edit.setPlaceholderText(placeholder)
        edit.setProperty("editTint", tint)
        edit.textChanged.connect(lambda _ed=edit, _t=tint: self._style_one_edit(_ed, _t))
        if key != "preview":
            edit.textChanged.connect(self._on_field_changed)
        self._style_one_edit(edit, tint)
        return edit

    def _make_preset_pane(self, key: str, tint: str) -> QFrame:
        pane = QFrame()
        pane.setObjectName(f"FemalePresetPane_{key}")
        pane.setFrameShape(QFrame.NoFrame)
        pane.setAttribute(Qt.WA_StyledBackground, True)
        pane.setProperty("paneTint", tint)
        col = QVBoxLayout(pane)
        col.setContentsMargins(8, 8, 8, 8)
        col.setSpacing(6)
        host = QWidget()
        host.setAttribute(Qt.WA_StyledBackground, True)
        host.setStyleSheet("background:transparent;border:none;")
        flow = FlowLayout(host, 0, 6, 6)
        col.addWidget(host, 1)
        add_btn = apply_mini_button(QPushButton("添加模块"))
        add_btn.setToolTip("添加自定义模块。点选使用，再点取消")
        add_btn.setFocusPolicy(Qt.NoFocus)
        add_btn.setFixedHeight(24)
        add_btn.clicked.connect(lambda _=False, k=key: self._add_module(k))
        self._add_btns.append(add_btn)
        col.addWidget(add_btn, 0, Qt.AlignLeft)
        self._preset_flows[key] = flow
        self._preset_panes[key] = pane
        self._style_preset_pane(pane, tint)
        self._style_add_btn(add_btn)
        self._rebuild_chips(key)
        return pane

    def _make_chip(self, key: str, name: str, value: str, custom: bool = False) -> QPushButton:
        btn = QPushButton(name)
        btn.setCheckable(True)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setProperty("chipKey", key)
        btn.setProperty("chipValue", value)
        btn.setProperty("chipName", name)
        btn.setProperty("chipCustom", bool(custom))
        btn.setFixedHeight(24)
        self._style_chip(btn)
        self._refresh_chip_caption(btn, on=False)
        btn.clicked.connect(lambda _=False, b=btn: self._on_chip_clicked(b))
        if custom:
            btn.setContextMenuPolicy(Qt.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, b=btn: self._chip_context_menu(b, pos)
            )
        return btn

    def _rebuild_chips(self, key: str):
        flow = self._preset_flows.get(key)
        if flow is None:
            return
        group = self._chip_groups.get(key)
        if group is None:
            group = QButtonGroup(self)
            group.setExclusive(False)
            self._chip_groups[key] = group
        for btn in list(group.buttons()):
            group.removeButton(btn)
        while flow.count():
            item = flow.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                self._chip_btns = [b for b in self._chip_btns if b is not w]
                w.setParent(None)
                w.deleteLater()
        for name, value in _PRESETS.get(key) or []:
            btn = self._make_chip(key, name, value, custom=False)
            group.addButton(btn)
            flow.addWidget(btn)
            self._chip_btns.append(btn)
        for item in self._custom.get(key) or []:
            name = str(item.get("name") or "").strip()
            value = str(item.get("value") or "").strip()
            if not name or not value:
                continue
            btn = self._make_chip(key, name, value, custom=True)
            group.addButton(btn)
            flow.addWidget(btn)
            self._chip_btns.append(btn)
        self._sync_chips()

    def _make_foot_btn(self, text: str, tip: str) -> QPushButton:
        btn = apply_mini_button(QPushButton(text))
        btn.setToolTip(tip)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        btn.setFixedHeight(_COPY_H)
        btn.setStyleSheet(self._copy_all_qss())
        return btn

    def _style_hint(self, lab: QLabel):
        lab.setStyleSheet(
            f"QLabel{{color:{tk('text_mut')};font-size:12px;"
            f"background:transparent;border:none;padding:0 2px;}}"
        )

    def _style_field_bar(self, bar: QFrame, lab: QLabel):
        bg = tk("row_bg")
        bar.setStyleSheet(
            f"QFrame#PromptFieldBar{{"
            f"background:{bg};border:none;border-radius:4px;}}"
        )
        lab.setStyleSheet(
            f"QLabel{{color:{tk('text')};font-size:{_TITLE_FONT}px;font-weight:700;"
            f"background:transparent;border:none;padding:0;}}"
        )
        pal = bar.palette()
        pal.setColor(QPalette.Window, QColor(bg))
        pal.setColor(QPalette.Base, QColor(bg))
        bar.setPalette(pal)

    def _style_preset_pane(self, pane: QFrame, tint: str):
        c = _edit_colors(tint)
        name = pane.objectName()
        pane.setStyleSheet(
            f"QFrame#{name}{{"
            f"background:{c['filled']};border:1px solid {c['border']};"
            f"border-radius:4px;}}"
        )

    def _style_one_edit(self, edit: QPlainTextEdit, tint: str):
        c = _edit_colors(tint)
        empty = not (edit.toPlainText() or "").strip()
        bg = c["empty"] if empty else c["filled"]
        fg = c["placeholder"] if empty else tk("text")
        name = edit.objectName()
        is_preview = edit is getattr(self, "_preview", None)
        color_css = "" if is_preview else f"color:{fg};"
        edit.setStyleSheet(
            f"QPlainTextEdit#{name}{{"
            f"background:{bg};{color_css}"
            f"border:none;border-radius:4px;"
            f"padding:{_EDIT_PAD_V}px {_EDIT_PAD_H}px;font-size:13px;}}"
            f"QPlainTextEdit#{name} QScrollBar:vertical,"
            f"QPlainTextEdit#{name} QScrollBar:horizontal{{"
            f"width:0px;height:0px;margin:0;}}"
        )
        pal = edit.palette()
        ph = QColor(c["placeholder"])
        try:
            pal.setColor(QPalette.PlaceholderText, ph)
        except Exception:
            pass
        pal.setColor(QPalette.Text, QColor(tk("text")))
        pal.setColor(QPalette.Base, QColor(bg))
        edit.setPalette(pal)

    def _style_chip(self, btn: QPushButton):
        h = self._hot_colors()
        custom = bool(btn.property("chipCustom"))
        if theme.is_dark:
            bd, bg, fg = "#3b5f9a", "#1a2c4e", tk("text")
            on_bd, on_bg, on_fg = "#16a34a", "#14532d", "#dcfce7"
            if custom:
                bd = "#2f8f55"
        else:
            bd, bg, fg = "#93c5fd", "#e8f1ff", tk("text")
            on_bd, on_bg, on_fg = "#15803d", "#dcfce7", "#14532d"
            if custom:
                bd = "#22c55e"
        btn.setStyleSheet(
            f"QPushButton{{"
            f"min-height:24px;max-height:24px;padding:0 10px;border-radius:6px;"
            f"border:1px solid {bd};background:{bg};color:{fg};"
            f"font-size:12px;font-weight:600;}}"
            f"QPushButton:hover{{"
            f"background:{h['hover_bg']};border-color:{h['hover_bd']};color:{h['hover_fg']};}}"
            f"QPushButton:pressed{{"
            f"background:{h['press_bg']};border-color:{h['press_bd']};color:{h['press_fg']};}}"
            f"QPushButton:checked{{"
            f"background:{on_bg};border-color:{on_bd};color:{on_fg};}}"
        )

    def _style_add_btn(self, btn: QPushButton):
        btn.setStyleSheet(self._mini_btn_qss())

    def _style_unlock_btn(self):
        btn = getattr(self, "btn_unlock", None)
        if btn is None:
            return
        locked = bool(self._body_locked)
        btn.setStyleSheet(self._lock_btn_qss())
        btn.setText("解锁" if locked else "锁定")
        if locked:
            btn.setToolTip("当前已锁定。点解锁后可改体型段")
            color = "#f59e0b"
        else:
            btn.setToolTip("当前可编辑。点锁定后恢复保护")
            color = "#22c55e"
        btn.setIcon(_lock_icon(locked, _LOCK_ICON, color))
        btn.setIconSize(QSize(_LOCK_ICON, _LOCK_ICON))

    @staticmethod
    def _hot_colors() -> dict:
        if theme.is_dark:
            return {
                "hover_bg": "#1d4ed8",
                "hover_bd": "#93c5fd",
                "hover_fg": "#f8fafc",
                "press_bg": "#2563eb",
                "press_bd": "#bfdbfe",
                "press_fg": "#ffffff",
            }
        return {
            "hover_bg": "#2563eb",
            "hover_bd": "#1d4ed8",
            "hover_fg": "#ffffff",
            "press_bg": "#1e40af",
            "press_bd": "#1e3a8a",
            "press_fg": "#ffffff",
        }

    @staticmethod
    def _copy_all_qss() -> str:
        h = PageFemaleChar._hot_colors()
        return (
            f"QPushButton{{"
            f"min-height:{_COPY_H}px;max-height:{_COPY_H}px;"
            f"padding:0 12px;border-radius:6px;"
            f"border:1px solid {tk('border')};"
            f"background:{tk('input_bg')};color:{tk('text')};"
            f"font-size:13px;font-weight:700;}}"
            f"QPushButton:hover{{"
            f"background:{h['hover_bg']};border-color:{h['hover_bd']};color:{h['hover_fg']};}}"
            f"QPushButton:pressed{{"
            f"background:{h['press_bg']};border-color:{h['press_bd']};color:{h['press_fg']};}}"
        )

    @staticmethod
    def _mini_btn_qss() -> str:
        h = PageFemaleChar._hot_colors()
        return (
            f"QPushButton{{"
            f"min-height:24px;max-height:24px;padding:0 12px;border-radius:6px;"
            f"border:1px solid {tk('border')};"
            f"background:{tk('input_bg')};color:{tk('text')};"
            f"font-size:12px;font-weight:700;}}"
            f"QPushButton:hover{{"
            f"background:{h['hover_bg']};border-color:{h['hover_bd']};color:{h['hover_fg']};}}"
            f"QPushButton:pressed{{"
            f"background:{h['press_bg']};border-color:{h['press_bd']};color:{h['press_fg']};}}"
        )

    @staticmethod
    def _lock_btn_qss() -> str:
        h = PageFemaleChar._hot_colors()
        return (
            f"QPushButton{{"
            f"min-height:24px;max-height:24px;min-width:76px;"
            f"padding:0 10px 0 6px;border-radius:6px;"
            f"border:1px solid {tk('border')};"
            f"background:{tk('input_bg')};color:{tk('text')};"
            f"font-size:12px;font-weight:700;}}"
            f"QPushButton:hover{{"
            f"background:{h['hover_bg']};border-color:{h['hover_bd']};color:{h['hover_fg']};}}"
            f"QPushButton:pressed{{"
            f"background:{h['press_bg']};border-color:{h['press_bd']};color:{h['press_fg']};}}"
        )

    def _field_values(self) -> Dict[str, str]:
        out = {}
        for key, _t, _p, _tok in _FIELDS:
            ed = self._edits.get(key)
            out[key] = (ed.toPlainText() if ed is not None else "") or ""
        return out

    def _body_text(self) -> str:
        ed = self._edits.get("body")
        if ed is None:
            return _BODY_DEFAULT
        return (ed.toPlainText() or "").strip() or _BODY_DEFAULT

    def _composed(self) -> str:
        text, _spans = compose_prompt_ex(self._field_values(), self._body_text())
        return text

    def _user_span_color(self) -> QColor:
        return QColor(_USER_BLUE_DARK if theme.is_dark else _USER_BLUE_LIGHT)

    def _apply_preview_spans(self, _edit: QPlainTextEdit, spans: List[Tuple[int, int]]):
        hl = getattr(self, "_preview_hl", None)
        if hl is None:
            return
        hl.set_spans(spans, self._user_span_color())

    def _on_field_changed(self, *_):
        if self._loading:
            return
        self._sync_chips()
        self._refresh_preview()
        self._schedule_persist()

    def _refresh_preview(self):
        preview = getattr(self, "_preview", None)
        if preview is None:
            return
        text, spans = compose_prompt_ex(self._field_values(), self._body_text())
        if preview.toPlainText() != text:
            preview.blockSignals(True)
            preview.setPlainText(text)
            preview.blockSignals(False)
            self._style_one_edit(preview, "blue")
        self._apply_preview_spans(preview, spans)
        fit = getattr(preview, "_fit_height", None)
        if callable(fit):
            fit()

    def _on_chip_clicked(self, btn: QPushButton):
        key = str(btn.property("chipKey") or "")
        value = str(btn.property("chipValue") or "")
        if self._chip_matches(btn):
            self._remove_chip_value(key, value)
        else:
            self._apply_chip(key, value)

    def _apply_chip(self, key: str, value: str):
        ed = self._edits.get(key)
        if ed is None:
            return
        if key == "camera":
            self._sync_chips()
            return
        if key == "body" and self._body_locked:
            self._sync_chips()
            return
        cur = (ed.toPlainText() or "").strip()
        value = (value or "").strip()
        if not value:
            self._sync_chips()
            return
        if key == "body" and value == _BODY_DEFAULT:
            # 「完整体型」是固定默认，点选即整体复位，不做逐段叠加
            ed.setPlainText(_BODY_DEFAULT)
            return
        # 点选 = 加入左侧编辑区：已有内容则用逗号接在后面，不替换
        if not cur or cur == value:
            ed.setPlainText(value)
        else:
            ed.setPlainText(cur + ", " + value)

    def _remove_chip_value(self, key: str, value: str):
        ed = self._edits.get(key)
        if ed is None:
            return
        if key == "camera":
            self._sync_chips()
            return
        if key == "body" and self._body_locked:
            self._sync_chips()
            return
        cur = (ed.toPlainText() or "").strip()
        value = (value or "").strip()
        if not value:
            self._sync_chips()
            return
        if key == "body" and value == _BODY_DEFAULT:
            if cur == _BODY_DEFAULT:
                ed.setPlainText("")
            return
        # 再点一次 = 取消使用：只摘掉该预设这段，保留其余内容
        ed.setPlainText(_strip_span(cur, value))

    def _chip_matches(self, btn: QPushButton) -> bool:
        key = str(btn.property("chipKey") or "")
        value = str(btn.property("chipValue") or "").strip()
        ed = self._edits.get(key)
        cur = ((ed.toPlainText() if ed is not None else "") or "").strip()
        if not value:
            return False
        if key == "body":
            if value == _BODY_DEFAULT:
                return cur == _BODY_DEFAULT
            if cur == _BODY_DEFAULT:
                return False
            return value in cur
        return value in cur

    def _refresh_chip_caption(self, btn: QPushButton, on: bool):
        name = str(btn.property("chipName") or "").strip() or (btn.text() or "").strip()
        value = str(btn.property("chipValue") or "").strip()
        btn.setText(name)
        if on:
            btn.setToolTip("使用中，再点一次取消\n" + value)
        else:
            btn.setToolTip("未使用，点选加入左侧\n" + value)

    def _sync_chips(self):
        for group in self._chip_groups.values():
            for btn in group.buttons():
                want = self._chip_matches(btn)
                if btn.isChecked() != want:
                    btn.blockSignals(True)
                    btn.setChecked(want)
                    btn.blockSignals(False)
                self._refresh_chip_caption(btn, want)

    def _add_module(self, key: str):
        dlg = _AddModuleDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            return
        name, value = dlg.pair()
        if not name or not value:
            return
        items = list(self._custom.get(key) or [])
        items.append({"name": name, "value": value})
        self._custom[key] = items
        self._rebuild_chips(key)
        self._apply_chip(key, value)
        self._schedule_persist()

    def _chip_context_menu(self, btn: QPushButton, pos):
        if not bool(btn.property("chipCustom")):
            return
        menu = QMenu(self)
        act = menu.addAction("删除模块")
        chosen = menu.exec_(btn.mapToGlobal(pos))
        if chosen is not act:
            return
        key = str(btn.property("chipKey") or "")
        value = str(btn.property("chipValue") or "")
        name = str(btn.property("chipName") or "").strip() or (btn.text() or "").strip()
        items = [
            it for it in (self._custom.get(key) or [])
            if not (
                str(it.get("name") or "").strip() == name
                and str(it.get("value") or "").strip() == value
            )
        ]
        self._custom[key] = items
        self._rebuild_chips(key)
        self._schedule_persist()

    def _toggle_body_lock(self):
        self._body_locked = not self._body_locked
        ed = self._edits.get("body")
        if ed is not None:
            ed.setReadOnly(self._body_locked)
        lab = getattr(self, "_body_title_lab", None)
        if lab is not None:
            lab.setText("体型（可编辑）" if not self._body_locked else "体型（建议锁定）")
        self._style_unlock_btn()
        self._schedule_persist()

    def _copy_prompt(self):
        text = self._composed()
        if not text:
            return
        QApplication.clipboard().setText(text)
        btn = getattr(self, "btn_copy", None)
        if btn is None:
            return
        old = btn.text()
        btn.setText("已复制")
        QTimer.singleShot(1200, lambda: btn.setText(old) if btn.text() == "已复制" else None)

    def restyle_theme(self, *_):
        hint = getattr(self, "_hint", None)
        if hint is not None:
            self._style_hint(hint)
        note = getattr(self, "_body_note", None)
        if note is not None:
            self._style_hint(note)
        for bar, lab in zip(self._title_bars, self._labels):
            self._style_field_bar(bar, lab)
        for ed in self._edits.values():
            tint = str(ed.property("editTint") or "green")
            self._style_one_edit(ed, tint)
        preview = getattr(self, "_preview", None)
        if preview is not None:
            self._style_one_edit(preview, "blue")
        for key, pane in self._preset_panes.items():
            tint = str(pane.property("paneTint") or "green")
            self._style_preset_pane(pane, tint)
        for btn in self._chip_btns:
            self._style_chip(btn)
        for btn in self._add_btns:
            self._style_add_btn(btn)
        copy_btn = getattr(self, "btn_copy", None)
        if copy_btn is not None:
            copy_btn.setStyleSheet(self._copy_all_qss())
        self._style_unlock_btn()
        self._refresh_preview()
        self._fit_all_edits()

    def on_enter(self):
        QTimer.singleShot(0, self._fit_all_edits)
        QTimer.singleShot(0, self._sync_foot_align)
        QTimer.singleShot(0, self._refresh_preview)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._sync_foot_align()

    def _sync_foot_align(self, *_):
        scroll = getattr(self, "_scroll", None)
        wrap = getattr(self, "_foot_wrap", None)
        if scroll is None or wrap is None:
            return
        gutter = max(0, scroll.width() - scroll.viewport().width())
        m = wrap.contentsMargins()
        if m.right() != gutter or m.left() != 0:
            wrap.setContentsMargins(0, 0, gutter, 0)

    def _fit_all_edits(self):
        for ed in list(self._edits.values()) + [getattr(self, "_preview", None)]:
            if ed is None:
                continue
            fit = getattr(ed, "_fit_height", None)
            if callable(fit):
                fit()

    def set_prefs_dirty_callback(self, cb):
        self._prefs_dirty_cb = cb

    def export_settings(self) -> dict:
        fields = {k: v.strip() for k, v in self._field_values().items()}
        custom = {}
        for key, items in self._custom.items():
            cleaned = []
            for it in items or []:
                name = str((it or {}).get("name") or "").strip()
                value = str((it or {}).get("value") or "").strip()
                if name and value:
                    cleaned.append({"name": name, "value": value})
            if cleaned:
                custom[key] = cleaned
        return {
            "fields": fields,
            "body": self._body_text(),
            "body_locked": bool(self._body_locked),
            "custom_modules": custom,
        }

    def apply_settings(self, d: dict):
        data = d if isinstance(d, dict) else {}
        fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
        body = str(data.get("body") or "").strip()
        fields, body = _migrate_saved_fields(fields, body)
        locked = data.get("body_locked")
        custom_in = data.get("custom_modules") if isinstance(data.get("custom_modules"), dict) else {}
        custom: Dict[str, List[dict]] = {}
        _custom_key_map = {"profession": "identity"}
        for key, items in custom_in.items():
            if not isinstance(items, list):
                continue
            cleaned = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                name = str(it.get("name") or "").strip()
                value = str(it.get("value") or "").strip()
                if name and value:
                    cleaned.append({"name": name, "value": value})
            if cleaned:
                dest = _custom_key_map.get(str(key), str(key))
                custom.setdefault(dest, []).extend(cleaned)
        self._custom = custom
        self._loading = True
        try:
            for key, _t, _p, _tok in _FIELDS:
                ed = self._edits.get(key)
                if ed is None:
                    continue
                ed.blockSignals(True)
                ed.setPlainText(str(fields.get(key) or ""))
                ed.blockSignals(False)
                self._style_one_edit(ed, str(ed.property("editTint") or "green"))
            bed = self._edits.get("body")
            if bed is not None:
                bed.blockSignals(True)
                bed.setPlainText(body)
                bed.blockSignals(False)
            self._body_locked = True if locked is None else bool(locked)
            if bed is not None:
                bed.setReadOnly(self._body_locked)
                self._style_one_edit(bed, "lock")
            lab = getattr(self, "_body_title_lab", None)
            if lab is not None:
                lab.setText("体型（可编辑）" if not self._body_locked else "体型（建议锁定）")
            self._style_unlock_btn()
            for key in list(self._preset_flows.keys()):
                self._rebuild_chips(key)
            self._refresh_preview()
        finally:
            self._loading = False
        QTimer.singleShot(0, self._fit_all_edits)

    def _schedule_persist(self):
        if self._loading:
            return
        self._save_timer.start()

    def _persist_now(self):
        cb = getattr(self, "_prefs_dirty_cb", None)
        if callable(cb):
            try:
                cb()
            except Exception:
                log.exception("通知 user.txt 保存女性角色模板失败")
