# pages/page_prompt_editor.py
# 文生视频提示词编辑：顶部分页（每页正文 / 预设分开存），
# 页内左栏填写（40%）/ 右栏预设（60%）。字段标题一字宽竖排，贴在内容左侧。

from __future__ import annotations

import json
import math
import os
import re
from typing import Dict, List, Tuple

from PyQt5.QtCore import (
    Qt, QTimer, QSize, QRectF, QPointF, QPoint, QRect, QEvent,
    QPropertyAnimation, QEasingCurve, QAbstractAnimation, QParallelAnimationGroup,
)
from PyQt5.QtGui import (
    QColor, QPalette, QTextDocument, QIcon, QPixmap, QPainter, QPen, QFont,
    QFontMetrics, QTextCursor, QPainterPath, QBrush,
)
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit,
    QScrollArea, QFrame, QSizePolicy, QApplication, QInputDialog, QMenu,
    QButtonGroup, QDialog, QDialogButtonBox, QFormLayout,
    QLineEdit, QTabWidget, QMessageBox,
)

from styles.style_all import (
    theme, tk, fmt, apply_mini_button,
    TAB_QSS, VIDEO_TAB_QSS, apply_transparent_surface,
    set_message_box_selectable,
)
from utils.flow_layout import FlowLayout
from utils.logger import get_logger

log = get_logger(__name__)

# key, 标题, 占位
_FIELDS: List[Tuple[str, str, str]] = [
    ("base", "基础参数", "时长、画幅、帧率，如 16:9 · 5 秒 · 24fps"),
    ("camera", "镜头", "机位、焦距、运镜，如 中景、缓慢推进"),
    ("person", "人物", "身份、五官，如 25 岁东亚女性、短发"),
    ("body", "体型", "身高围度肌肉，如 高挑、宽肩、运动员体型"),
    ("outfit", "服装", "款式材质颜色，如 黑色皮衣、白色运动鞋"),
    ("accessories", "装备", "职业相关装备，如 医疗扫描仪、工具腰带"),
    ("scene", "环境", "地点时间天气，如 雨夜霓虹街道"),
    ("scene_detail", "场景细节", "次要环境细节，如 全息屏、雾气、灯光"),
    ("action", "动作", "肢体动作与节奏，如 转身回望、快步走过"),
    ("performance", "表演", "表情情绪微动作，如 隐忍后释然一笑"),
    ("line", "台词", "口播或对白，注明语言与语气"),
    ("palette", "色彩", "主色与点缀，如 cyan, magenta, gold"),
    ("quality", "画面质量", "灯光、景深、质感，如 电影感、浅景深、8K"),
]

_XFER_BTN = 18
_XFER_W = 22  # 比按钮宽一圈，避免 1px 描边被父级裁切
_PASTE_ICON = 12
_COPY_H = 33
_HIST_ICON = 18
_EDIT_PAD_V = 6   # 与样式 padding 上下一致
_EDIT_PAD_H = 8
_TITLE_FONT = 16  # 保持原字号；上下 padding 8→2 后再加 6
_TITLE_PAD_V = 5
_TITLE_ACT = 18   # 标题栏上的粘贴 / 冻结按钮
_TITLE_BAR_PAD_H = 4  # 左右留白，避免 1px 描边被标题栏裁切
_TITLE_COL_GAP = 3
_HIST_MAX = 5     # 撤销最多 5 步，仅当前页、不落盘
_CUSTOM_PLACEHOLDER = "补充说明，自由填写"
_ORDER_DRAG_PX = 6
_ORDER_CARD_H = 78
_PRESET_PAD = 0

# 每个分区一张内容卡的主色；标题栏同色，编辑区再淡 80%。
_FIELD_ACCENT = {
    "base": "#3B82F6",
    "camera": "#06B6D4",
    "person": "#F472B6",
    "body": "#F59E0B",
    "outfit": "#8B5CF6",
    "accessories": "#F97316",
    "scene": "#22C55E",
    "scene_detail": "#14B8A6",
    "action": "#EF4444",
    "performance": "#D946EF",
    "line": "#6366F1",
    "palette": "#EAB308",
    "quality": "#84CC16",
}
_CUSTOM_ACCENTS = (
    "#FB7185", "#A78BFA", "#34D399", "#FB923C", "#38BDF8", "#F43F5E",
)

# 提示词字段 → 角色模板预设 key。
_CHAR_PRESET_KEYS: Dict[str, Tuple[str, ...]] = {
    "person": ("identity", "face"),
    "body": ("body",),
    "outfit": ("outfit",),
    "accessories": ("accessories",),
    "scene": ("scene",),
    "scene_detail": ("scene_detail",),
    "action": ("pose",),
    "performance": ("expression",),
    "camera": ("camera",),
    "palette": ("palette",),
}

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

_CHAR_PRESETS: Dict[str, List[Tuple[str, str]]] = {
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
    "body": [
        ("完整体型", _BODY_DEFAULT),
        ("粗腿+肌群", "extremely thick, heavily muscular thighs with massive quadriceps and powerful hamstrings"),
        ("体积对比", "substantial lower-body muscle volume clearly beyond that of an ordinary athletic woman"),
        ("细腰反差", "a dramatically narrow waist creating a pronounced contrast with the powerful lower body"),
        ("重量感胸部", "an extremely large, full, heavy natural-looking bust, approximately twice the volume of a typical curvy figure, fully covered by clothing with no deliberate cleavage exposure"),
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
}
_ORDER_GUIDE_W = 12
_ORDER_SLIDE_MS = 200
_ORDER_DROP_MS = 180

# ===== 预设 txt：docs/ 下两份「【分类】+ 序号. 标题 | 内容」，启动时读一次 =====
# （旧版放在项目根 / exe 旁，仍兼容回退查找）
# 内置预设读不到 / 没有条目时回退到上面的 _CHAR_PRESETS；H3 读不到就不显示 H3 按钮。
_PRESET_TXT_BUILTIN = "内置预设_提示词分类.txt"
_PRESET_TXT_H3 = "MiniMAX_H3_提示词分类.txt"
# 每栏「自定义 + 内置 + H3」按内容去重合并：前 _TOP_N 条直接显示，其余折进「H3 ▸」；
# 最近加进正文的条目排最前（MRU，存 prompt_editor.preset_mru，每栏最多 _MRU_MAX 条）
_TOP_N = 15
_MRU_MAX = 200
# 每行标题栏右侧：左栏编辑框 / 右栏预设 按可用宽度 40% / 60% 分
_LEFT_RATIO = 0.40
_PRESET_TXT_CATS: Dict[str, str] = {
    "基础": "base",
    "基础参数": "base",
    "镜头": "camera",
    "人物": "person",
    "体型": "body",
    "服装": "outfit",
    "装备": "accessories",
    "环境": "scene",
    "场景细节": "scene_detail",
    "动作": "action",
    "表演": "performance",
    "台词": "line",
    "色彩": "palette",
    "画面质量": "quality",
}
_PRESET_TXT_HEAD_RE = re.compile(r"^【\s*(.+?)\s*】$")
_PRESET_TXT_ITEM_RE = re.compile(r"^\d+\s*[.．]\s*(.+)$")
_PRESET_TXT_SKIP_LOG_MAX = 20
_PRESET_TXT_CACHE: dict = {}


def _preset_txt_path(name: str) -> str:
    """先找各根目录下的 docs/，再回退旧位置（根目录本身）。

    根目录顺序：app_root（exe 旁 / 源码根）→ resource_root（打包资源）→ 本文件上级目录。
    """
    roots = []
    try:
        from utils.app_paths import app_root, resource_root
        roots.append(app_root())
        roots.append(resource_root())
    except Exception:
        pass
    roots.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    roots = [r for r in roots if r]
    dirs = [os.path.join(r, "docs") for r in roots] + roots
    seen = set()
    for d in dirs:
        norm = os.path.normcase(os.path.abspath(d))
        if norm in seen:
            continue
        seen.add(norm)
        path = os.path.join(d, name)
        if os.path.isfile(path):
            return path
    return ""


def _parse_preset_txt(path: str):
    """解析一份预设 txt → {字段key: [(标题, 内容), ...]}；文件读不了返回 None。

    空行跳过；乱码 / 不在已知分类下 / 缺「 | 」的行跳过并记日志；按第一个「 | 」切分。
    """
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception:
        log.exception("读取预设 txt 失败：%s", path)
        return None
    text = raw.decode("utf-8-sig", errors="replace")
    out: Dict[str, List[Tuple[str, str]]] = {
        k: [] for k in dict.fromkeys(_PRESET_TXT_CATS.values())
    }
    cur = None
    skipped = 0
    base = os.path.basename(path)

    def _skip(ln: int, why: str):
        nonlocal skipped
        skipped += 1
        if skipped <= _PRESET_TXT_SKIP_LOG_MAX:
            log.warning("预设 txt %s 第 %d 行已跳过（%s）", base, ln, why)

    for ln, line in enumerate(text.splitlines(), 1):
        s = line.replace("\ufeff", "").strip()
        if not s:
            continue
        if "\ufffd" in s:
            _skip(ln, "乱码")
            continue
        m = _PRESET_TXT_HEAD_RE.match(s)
        if m:
            cur = _PRESET_TXT_CATS.get(m.group(1))
            if cur is None:
                _skip(ln, "未知分类")
            continue
        if cur is None:
            _skip(ln, "不在已知分类下")
            continue
        m = _PRESET_TXT_ITEM_RE.match(s)
        if not m:
            _skip(ln, "格式不符")
            continue
        body = m.group(1)
        sep = " | " if " | " in body else ("|" if "|" in body else "")
        if not sep:
            _skip(ln, "缺少 | 分隔")
            continue
        name, value = body.split(sep, 1)
        name = name.strip()
        value = value.strip()
        if not name or not value:
            _skip(ln, "标题或内容为空")
            continue
        out[cur].append((name, value))
    if skipped > _PRESET_TXT_SKIP_LOG_MAX:
        log.warning("预设 txt %s 共跳过 %d 行", base, skipped)
    return out


def _txt_presets(reload: bool = False):
    """(内置 or None, H3)。进程内只读一次；内置为 None 表示回退代码里的 _CHAR_PRESETS。"""
    if reload:
        _PRESET_TXT_CACHE.clear()
    data = _PRESET_TXT_CACHE.get("data")
    if data is not None:
        return data
    builtin = None
    path = _preset_txt_path(_PRESET_TXT_BUILTIN)
    if path:
        parsed = _parse_preset_txt(path)
        if parsed and any(parsed.values()):
            builtin = parsed
        else:
            log.warning("内置预设 txt 无可用条目，改用代码内置预设：%s", path)
    else:
        log.info("未找到 %s，改用代码内置预设", _PRESET_TXT_BUILTIN)
    h3: Dict[str, List[Tuple[str, str]]] = {}
    path = _preset_txt_path(_PRESET_TXT_H3)
    if path:
        parsed = _parse_preset_txt(path)
        if parsed:
            h3 = {k: v for k, v in parsed.items() if v}
    else:
        log.info("未找到 %s，不显示 H3 预设", _PRESET_TXT_H3)
    log.info(
        "提示词预设 txt：内置 %s 条，H3 %d 条",
        "回退" if builtin is None else sum(len(v) for v in builtin.values()),
        sum(len(v) for v in h3.values()),
    )
    data = (builtin, h3)
    _PRESET_TXT_CACHE["data"] = data
    return data


# ===== 预设按钮绿色判定：按分隔段整段比较 =====
# 正文按 , ， ; ； 换行 切段；预设内容也切段，连续几段完全相等才算命中。
# 内容本身带逗号也能整体命中；短条若只落在某条更长预设的命中范围内，不算（长条优先）。
_CHIP_SEP_CHARS = ",，;；\n\r"
_CHIP_SEG_RE = re.compile(r"[^,，;；\n\r]+")
_CHIP_PAD = " \t\u3000\r"


def _chip_segments(text: str) -> List[Tuple[str, int, int]]:
    out = []
    for m in _CHIP_SEG_RE.finditer(text or ""):
        raw = m.group(0)
        s = raw.strip()
        if not s:
            continue
        st = m.start() + (len(raw) - len(raw.lstrip()))
        out.append((s, st, st + len(s)))
    return out


_CHIP_VALUE_SEGS: Dict[str, Tuple[str, ...]] = {}


def _chip_value_segs(value: str) -> Tuple[str, ...]:
    segs = _CHIP_VALUE_SEGS.get(value)
    if segs is None:
        segs = tuple(s for s, _a, _b in _chip_segments(value))
        if len(_CHIP_VALUE_SEGS) < 8192:
            _CHIP_VALUE_SEGS[value] = segs
    return segs


def _chip_all_runs(text: str, values):
    """返回 (正文分段, {预设内容: [(起段, 止段), ...]})。"""
    segs = _chip_segments(text)
    words = [s for s, _a, _b in segs]
    index: Dict[str, List[int]] = {}
    for i, w in enumerate(words):
        index.setdefault(w, []).append(i)
    runs: Dict[str, List[Tuple[int, int]]] = {}
    for v in values or []:
        v = (v or "").strip()
        if not v or v in runs:
            continue
        vs = _chip_value_segs(v)
        n = len(vs)
        if not n:
            continue
        hit = [
            (i, i + n) for i in index.get(vs[0], ())
            if tuple(words[i:i + n]) == vs
        ]
        if hit:
            runs[v] = hit
    return segs, runs


def _chip_free_runs(runs: dict, value: str) -> List[Tuple[int, int]]:
    """value 的命中里，没有被别的更长预设整段包住的那些。"""
    others = [(i, j) for v, rs in runs.items() if v != value for i, j in rs]
    out = []
    for i, j in runs.get(value, ()):
        if any(oi <= i and j <= oj and (oj - oi) > (j - i) for oi, oj in others):
            continue
        out.append((i, j))
    return out


def _active_chip_values(text: str, values) -> set:
    _segs, runs = _chip_all_runs(text, values)
    return {v for v in runs if _chip_free_runs(runs, v)}


def _strip_chip_segments(text: str, value: str, values=None) -> str:
    """去掉正文里这条预设命中的整段（连同一侧的一个分隔符），别的段原样保留。"""
    out = text or ""
    value = (value or "").strip()
    if not value:
        return out
    pool = list(values or [])
    if value not in pool:
        pool.append(value)
    for _ in range(64):
        segs, runs = _chip_all_runs(out, pool)
        free = _chip_free_runs(runs, value)
        if not free:
            break
        i, j = free[0]
        st, en = segs[i][1], segs[j - 1][2]
        before = out[:st].rstrip(_CHIP_PAD)
        after = out[en:].lstrip(_CHIP_PAD)
        if before and before[-1] in _CHIP_SEP_CHARS:
            before = before[:-1].rstrip(_CHIP_PAD)
        elif after and after[0] in _CHIP_SEP_CHARS:
            after = after[1:].lstrip(_CHIP_PAD)
        out = before + after
    return out.strip()


def _join_chip_value(cur: str, value: str) -> str:
    """追加一条预设：两边都是英文用「, 」，否则用中文「，」。"""
    cur = (cur or "").rstrip()
    value = (value or "").strip()
    if not value:
        return cur
    if not cur:
        return value
    ascii_pair = cur[-1].isascii() and value[0].isascii()
    if cur[-1] in _CHIP_SEP_CHARS:
        return cur + (" " if ascii_pair else "") + value
    return cur + (", " if ascii_pair else "，") + value

def _legacy_editor_paths() -> list:
    """旧提示词文件候选路径（已迁入 user.txt，仅作一次性回读）。

    主位置 records/prompts_editor.json；兼容旧 data/ 与项目根 prompts/。
    """
    paths = []
    try:
        from utils.app_paths import records_dir, data_dir, app_root
        paths.append(os.path.join(records_dir(), "prompts_editor.json"))
        paths.append(os.path.join(data_dir(), "prompts_editor.json"))
        paths.append(os.path.join(data_dir(), "prompts", "editor.json"))
        paths.append(os.path.join(app_root(), "prompts", "editor.json"))
    except Exception:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        paths.append(os.path.join(here, "records", "prompts_editor.json"))
        paths.append(os.path.join(here, "data", "prompts_editor.json"))
        paths.append(os.path.join(here, "data", "prompts", "editor.json"))
        paths.append(os.path.join(here, "prompts", "editor.json"))
    return paths


def _catalog_map() -> Dict[str, Tuple[str, str, str]]:
    return {k: (k, t, p) for k, t, p in _FIELDS}


_PAGE_TITLE_MAX = 24
_PAGE_SETTING_KEYS = (
    "orig", "new", "field_order", "custom_fields",
    "field_titles", "custom_presets", "frozen_fields",
)


def _clean_page_title(text: str, fallback: str) -> str:
    title = " ".join(str(text or "").replace("\n", " ").replace("\r", " ").split())
    if len(title) > _PAGE_TITLE_MAX:
        title = title[:_PAGE_TITLE_MAX].rstrip()
    return title or fallback


def _clean_page_id(raw, fallback: str) -> str:
    s = str(raw or "").strip()
    if (
        s
        and len(s) <= 32
        and all(c.isalnum() or c in "-_" for c in s)
    ):
        return s
    return fallback


def _fresh_page_id(used) -> str:
    n = 1
    while f"p{n}" in used:
        n += 1
    return f"p{n}"


def _blank_prompt_page(page_id: str, title: str) -> dict:
    return {
        "id": page_id,
        "title": title,
        "orig": {},
        "new": {},
        "field_order": [],
        "custom_fields": [],
        "field_titles": {},
        "custom_presets": {},
        "frozen_fields": [],
    }


def _copy_page_settings(src: dict) -> dict:
    if not isinstance(src, dict):
        return {}
    return {k: src.get(k) for k in _PAGE_SETTING_KEYS if k in src}


def _prompt_dict_has_body(data: dict) -> bool:
    """这一份里有没有正文、预设或字段结构。空壳页不算。"""
    if not isinstance(data, dict):
        return False
    for side in ("orig", "new"):
        block = data.get(side)
        if isinstance(block, dict) and any(str(v or "").strip() for v in block.values()):
            return True
    presets = data.get("custom_presets")
    if isinstance(presets, dict) and presets:
        return True
    if isinstance(data.get("custom_fields"), list) and data.get("custom_fields"):
        return True
    if isinstance(data.get("field_order"), list) and data.get("field_order"):
        return True
    titles = data.get("field_titles")
    if isinstance(titles, dict) and any(str(v or "").strip() for v in titles.values()):
        return True
    frozen = data.get("frozen_fields")
    if isinstance(frozen, list) and frozen:
        return True
    return False


def prompt_pages_from_store(data) -> Tuple[List[dict], str, bool]:
    """把 user.txt 里的提示词段收成页列表。

    返回 (pages, active_id, migrate_legacy_file)。
    旧的单页字段（orig / custom_presets / …）并进标题为「默认」的第一页。
    完全没有内容时，才允许第一页去读一次旧 editor.json。
    """
    data = data if isinstance(data, dict) else {}
    raw = data.get("pages")
    pages: List[dict] = []
    used = set()
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            pid = _clean_page_id(item.get("id"), "")
            if not pid or pid in used:
                pid = _fresh_page_id(used)
            used.add(pid)
            title = _clean_page_title(item.get("title"), f"分类{len(pages) + 1}")
            page = _blank_prompt_page(pid, title)
            page.update(_copy_page_settings(item))
            page["id"] = pid
            page["title"] = title
            pages.append(page)
    # 默认结构会塞进空的 pages。有旧正文时以旧正文为准，别被空壳盖掉。
    if pages and not any(_prompt_dict_has_body(p) for p in pages) and _prompt_dict_has_body(data):
        pages = []
    if pages:
        ids = {p["id"] for p in pages}
        active = str(data.get("active") or "").strip()
        if active not in ids:
            active = pages[0]["id"]
        return pages, active, False
    if _prompt_dict_has_body(data):
        page = _blank_prompt_page("p1", "默认")
        page.update(_copy_page_settings(data))
        page["id"] = "p1"
        page["title"] = "默认"
        return [page], "p1", False
    return [_blank_prompt_page("p1", "默认")], "p1", True


def _confirm_delete_page(parent, title: str) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("删除分页")
    box.setText(f"删除「{title}」？")
    box.setInformativeText("这一页的正文和预设都会去掉，不能恢复。")
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.setDefaultButton(QMessageBox.No)
    try:
        set_message_box_selectable(box)
    except Exception:
        pass
    return box.exec_() == QMessageBox.Yes


def _with_missing_builtins(specs: List[Tuple[str, str, str]]) -> List[Tuple[str, str, str]]:
    """出厂新增字段按默认相对位置插入，已有自定义顺序不动。"""
    out = list(specs or [])
    seen = {s[0] for s in out}
    factory = list(_FIELDS)
    factory_keys = [s[0] for s in factory]
    cat = {s[0]: s for s in factory}
    for k in factory_keys:
        if k in seen:
            continue
        idx = factory_keys.index(k)
        insert_at = len(out)
        for prev in reversed(factory_keys[:idx]):
            if prev not in seen:
                continue
            for i, spec in enumerate(out):
                if spec[0] == prev:
                    insert_at = i + 1
                    break
            break
        out.insert(insert_at, cat[k])
        seen.add(k)
    return out


def _order_card_font() -> QFont:
    """底栏内容卡：11px，每行按这个字号量 3 个汉字。"""
    f = QFont()
    f.setPixelSize(11)
    f.setWeight(QFont.DemiBold)
    return f


def _order_card_line_width() -> int:
    """一行正好容下 3 个汉字，第 4 个换行。"""
    fm = QFontMetrics(_order_card_font())
    three = int(fm.horizontalAdvance("基础参"))
    one = max(1, int(fm.horizontalAdvance("参")))
    return three + max(1, one // 5)


def _order_card_w() -> int:
    # 左右边距 4+4，再留边框，避免第 3 个字被边裁掉
    return _order_card_line_width() + 8 + 6


def _merge_preset_maps(a: dict, b: dict) -> dict:
    """两份自定义预设按（分区, 标题, 内容）并成一份，不丢、不重复。"""
    out: Dict[str, List[dict]] = {}

    def _add(src):
        if not isinstance(src, dict):
            return
        for key, items in src.items():
            dest = out.setdefault(str(key), [])
            have = {
                (
                    str((it or {}).get("name") or "").strip(),
                    str((it or {}).get("value") or "").strip(),
                )
                for it in dest
            }
            for it in items or []:
                if not isinstance(it, dict):
                    continue
                name = str(it.get("name") or "").strip()
                value = str(it.get("value") or "").strip()
                if not name or not value or (name, value) in have:
                    continue
                dest.append({"name": name, "value": value})
                have.add((name, value))

    _add(a)
    _add(b)
    return {k: v for k, v in out.items() if v}


def _title_label_font() -> QFont:
    f = QFont()
    f.setPixelSize(_TITLE_FONT)
    f.setBold(True)
    return f


def _title_line_height() -> int:
    """竖排标题行高：默认行距再收 2px。"""
    return max(10, int(QFontMetrics(_title_label_font()).lineSpacing()) - 2)


class _VTitle(QWidget):
    """一字一行竖排标题；自绘行距，不走 QLabel 的 HTML 行高。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PromptFieldTitle")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background:transparent;border:none;")
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self._raw = ""
        self._chars: List[str] = []
        self._fg = QColor("#f8fafc")

    def set_title(self, title: str):
        self._raw = (title or "").strip()
        self._chars = [ch for ch in self._raw if not ch.isspace()]
        h = max(1, len(self._chars)) * _title_line_height()
        self.setFixedHeight(h)
        self.updateGeometry()
        self.update()

    def raw_title(self) -> str:
        return self._raw

    def set_color(self, color: str):
        self._fg = QColor(color)
        self.update()

    def sizeHint(self):
        w = _title_inner_width()
        n = max(1, len(self._chars))
        return QSize(w, n * _title_line_height())

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        p.setFont(_title_label_font())
        p.setPen(self._fg)
        step = _title_line_height()
        w = self.width()
        for i, ch in enumerate(self._chars):
            p.drawText(QRect(0, i * step, w, step), Qt.AlignHCenter | Qt.AlignVCenter, ch)


def _title_inner_width(lab: QLabel = None) -> int:
    """竖排标题 / 按钮的内容宽（不含标题栏左右留白）。"""
    fm = lab.fontMetrics() if lab is not None else QFontMetrics(_title_label_font())
    w = _TITLE_ACT
    for ch in "基础参数镜头人物体型服装装备环境场景细节动作表演台词色彩画面质量完整提示词字":
        w = max(w, int(fm.horizontalAdvance(ch)))
    return w


def _title_col_width(lab: QLabel = None) -> int:
    """标题栏总宽：内容 + 左右留白。"""
    return _title_inner_width(lab) + 2 * _TITLE_BAR_PAD_H


def _field_accent(key: str) -> str:
    k = str(key or "")
    if k in _FIELD_ACCENT:
        return _FIELD_ACCENT[k]
    if k.startswith("custom_"):
        try:
            n = int(k.split("_", 1)[1])
        except (TypeError, ValueError):
            n = 0
        return _CUSTOM_ACCENTS[n % len(_CUSTOM_ACCENTS)]
    if not k or k == "full":
        return _FIELD_ACCENT["base"]
    h = sum(ord(c) for c in k)
    return _CUSTOM_ACCENTS[h % len(_CUSTOM_ACCENTS)]


def _mix_hex(a: str, b: str, t: float) -> str:
    """t=0 全 a，t=1 全 b。"""
    ca, cb = QColor(a), QColor(b)
    t = max(0.0, min(1.0, float(t)))
    r = int(ca.red() * (1.0 - t) + cb.red() * t + 0.5)
    g = int(ca.green() * (1.0 - t) + cb.green() * t + 0.5)
    bl = int(ca.blue() * (1.0 - t) + cb.blue() * t + 0.5)
    return QColor(r, g, bl).name()


def _fade_toward() -> str:
    if theme.is_dark:
        try:
            return str(tk("input_bg") or "#0f172a")
        except Exception:
            return "#0f172a"
    return "#ffffff"


def _fade80(color: str) -> str:
    """主色变淡 80%（八成靠向底色）。"""
    return _mix_hex(color, _fade_toward(), 0.80)


def _contrast_fg(bg: str) -> str:
    c = QColor(bg)
    luma = (0.2126 * c.red() + 0.7152 * c.green() + 0.0722 * c.blue()) / 255.0
    return "#0f172a" if luma > 0.55 else "#f8fafc"


def _empty_side(keys=None) -> Dict[str, str]:
    if keys is None:
        keys = [k for k, _t, _p in _FIELDS]
    return {k: "" for k in keys}


# 全角 ASCII（！＂＃…～）+ 常见中文标点 → 半角；汉字本身不动。
_FW_PUNCT = str.maketrans({
    "\u3000": " ",   # 全角空格
    "\u3001": ",",   # 、
    "\u3002": ".",   # 。
    "\u3008": "<",   # 〈
    "\u3009": ">",   # 〉
    "\u300a": "<",   # 《
    "\u300b": ">",   # 》
    "\u300c": '"',   # 「
    "\u300d": '"',   # 」
    "\u300e": '"',   # 『
    "\u300f": '"',   # 』
    "\u3010": "[",   # 【
    "\u3011": "]",   # 】
    "\u3014": "[",   # 〔
    "\u3015": "]",   # 〕
    "\u3016": "[",   # 〖
    "\u3017": "]",   # 〗
    "\uffe5": "\u00a5",  # ￥ → ¥
})


def _fullwidth_symbols_to_halfwidth(text: str) -> str:
    if not text:
        return text
    chars = []
    for ch in text:
        o = ord(ch)
        if 0xFF01 <= o <= 0xFF5E:
            chars.append(chr(o - 0xFEE0))
        else:
            chars.append(ch)
    return "".join(chars).translate(_FW_PUNCT)


def _normalize_prompt_paste(text: str) -> str:
    """粘贴规范：全角符号转半角，去掉段首空白。"""
    text = _fullwidth_symbols_to_halfwidth(text or "")
    return text.lstrip(" \t\r\n")


def _clipboard_icon(size: int, color: str) -> QIcon:
    """线框剪贴板，给中间列粘贴按钮用。"""
    size = max(12, int(size))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    s = float(size)
    pen = QPen(QColor(color), max(1.3, s * 0.09))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(s * 0.20, s * 0.28, s * 0.60, s * 0.58), s * 0.08, s * 0.08)
    p.drawRoundedRect(QRectF(s * 0.33, s * 0.12, s * 0.34, s * 0.22), s * 0.06, s * 0.06)
    p.drawLine(QPointF(s * 0.34, s * 0.50), QPointF(s * 0.66, s * 0.50))
    p.drawLine(QPointF(s * 0.34, s * 0.64), QPointF(s * 0.58, s * 0.64))
    p.end()
    return QIcon(pm)


def _hist_icon(kind: str, size: int, color: str) -> QIcon:
    """撤销=向左箭头，重做=向右箭头。"""
    size = max(14, int(size))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    s = float(size)
    c = QColor(color)
    cy = s * 0.5
    to_left = kind == "undo"
    # 箭头最左/最右（tip）坐标
    if to_left:
        tip_x = s * 0.16
        far_x = s * 0.82
    else:
        tip_x = s * 0.84
        far_x = s * 0.18
    head = s * 0.24          # 三角头长度
    half = s * 0.17          # 三角头半高
    if to_left:
        base_x = tip_x + head
    else:
        base_x = tip_x - head
    pen = QPen(c, max(1.5, s * 0.08))
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    # 箭杆：从 far 端到三角头底边
    p.drawLine(QPointF(far_x, cy), QPointF(base_x, cy))
    # 三角头（填充）
    tri = QPainterPath()
    tri.moveTo(tip_x, cy)
    tri.lineTo(base_x, cy - half)
    tri.lineTo(base_x, cy + half)
    tri.closeSubpath()
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(c))
    p.drawPath(tri)
    p.end()
    return QIcon(pm)


def _freeze_icon(frozen: bool, size: int, color: str) -> QIcon:
    """可见性图标：未冻结=睁眼，冻结=闭眼。"""
    size = max(12, int(size))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    s = float(size)
    c = QColor(color)
    pen = QPen(c, max(1.2, s * 0.10))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    cx, cy = s * 0.50, s * 0.52
    w, h = s * 0.80, s * 0.46
    x0, y0 = (s - w) / 2.0, cy - h / 2.0
    if frozen:
        # 闭眼：中段向下弯的下眼睑弧线
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(x0, cy)
        path.quadTo(cx, cy + h * 0.62, x0 + w, cy)
        p.drawPath(path)
    else:
        # 睁眼：杏核轮廓 + 实心瞳孔
        path = QPainterPath()
        path.moveTo(x0, cy)
        path.cubicTo(x0, y0, x0 + w, y0, x0 + w, cy)
        path.cubicTo(x0 + w, y0 + h, x0, y0 + h, x0, cy)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        pr = max(1.0, s * 0.085)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(c))
        p.drawEllipse(QPointF(cx, cy), pr, pr)
    p.end()
    return QIcon(pm)


def _strip_chip_value(text: str, value: str) -> str:
    """从正文里拿掉预设原文，并清掉旁边多出来的逗号。"""
    raw = text or ""
    value = (value or "").strip()
    if not value or value not in raw:
        return raw
    if raw.strip() == value:
        return ""
    out = raw
    while value in out:
        prev = out
        i = out.find(value)
        before = out[:i].rstrip()
        after = out[i + len(value):].lstrip()
        if before.endswith(","):
            before = before[:-1].rstrip()
        if after.startswith(","):
            after = after[1:].lstrip()
        if before and after:
            out = before + ", " + after
        else:
            out = before or after
        if out == prev:
            break
    return out.strip()


def _fit_card_text(text: str, fm, width: int, max_lines: int) -> str:
    """按宽度折行；超出行数时末行右侧截断。"""
    raw = (text or "").strip()
    if not raw:
        return ""
    width = max(8, int(width))
    max_lines = max(1, int(max_lines))

    def _w(s: str) -> int:
        try:
            return int(fm.horizontalAdvance(s))
        except Exception:
            return int(fm.width(s))

    lines = []
    cur = ""
    for ch in raw:
        trial = cur + ch
        if not cur or _w(trial) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    if len(lines) <= max_lines:
        return "\n".join(lines)
    kept = lines[:max_lines]
    rest = "".join(lines[max_lines - 1:])
    kept[-1] = fm.elidedText(rest, Qt.ElideRight, width)
    return "\n".join(kept)


def _join_old(*parts: str) -> str:
    return "\n".join(p for p in (s.strip() for s in parts) if p)


def _migrate_side(data: Dict[str, str], keys=None) -> Dict[str, str]:
    """旧 12 项并入现 10 项；已是新格式则原样读取（缺的体型为空）。"""
    key_list = list(keys) if keys is not None else [k for k, _t, _p in _FIELDS]
    out = _empty_side(key_list)
    if not data:
        return out
    if "person" in data or "quality" in data or any(str(k).startswith("custom_") for k in data):
        for k in key_list:
            out[k] = str(data.get(k) or "").strip()
        return out
    mapped = {
        "base": str(data.get("base") or "").strip(),
        "camera": str(data.get("camera") or "").strip(),
        "person": str(data.get("identity") or "").strip(),
        "body": str(data.get("body") or "").strip(),
        "outfit": str(data.get("outfit") or "").strip(),
        "scene": str(data.get("scene") or "").strip(),
        "action": str(data.get("action") or "").strip(),
        "performance": str(data.get("performance") or "").strip(),
        "line": str(data.get("line") or "").strip(),
        "quality": _join_old(
            str(data.get("light") or ""),
            str(data.get("detail") or ""),
            str(data.get("render") or ""),
        ),
    }
    for k in key_list:
        if k in mapped:
            out[k] = mapped[k]
        else:
            out[k] = str(data.get(k) or "").strip()
    return out


def _tint_colors(kind: str) -> dict:
    """原提示词=蓝底，新提示词=绿底。"""
    dark = theme.is_dark
    if kind == "green":
        if dark:
            return {
                "card": "#10261a",
                "border": "#2f8f55",
                "empty": "#07140e",
                "filled": "#163528",
                "placeholder": "#1c2c24",
            }
        return {
            "card": "#dcfce7",
            "border": "#22c55e",
            "empty": "#9ec3a8",
            "filled": "#f4fdf7",
            "placeholder": "#87a894",
        }
    # blue
    if dark:
        return {
            "card": "#10243d",
            "border": "#3b82f6",
            "empty": "#071018",
            "filled": "#16324f",
            "placeholder": "#1a2634",
        }
    return {
        "card": "#dbeafe",
        "border": "#3b82f6",
        "empty": "#9bb0cc",
        "filled": "#f4f8ff",
        "placeholder": "#87a0b8",
    }


class _GrowEdit(QPlainTextEdit):
    """最少 1 行正文 + 底下空 1 行；按内容长高。左右成对时取较高一侧，文字顶对齐。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTabChangesFocus(True)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._meas = QTextDocument(self)
        self._pair = None
        self._fitting = False
        self.normalize_paste = False
        self.textChanged.connect(self._fit_height)
        self._fit_height()

    def set_pair(self, other: "_GrowEdit"):
        self._pair = other

    def insertFromMimeData(self, source):
        if self.normalize_paste and source is not None and source.hasText():
            self.insertPlainText(_normalize_prompt_paste(source.text()))
            return
        super().insertFromMimeData(source)

    def _chrome(self) -> int:
        m = self.contentsMargins()
        dm = int(self.document().documentMargin())
        return (
            m.top() + m.bottom()
            + 2 * self.frameWidth()
            + _EDIT_PAD_V * 2
            + dm * 2
            + 2
        )

    def _min_h(self) -> int:
        # 1 行正文 + 底下空 1 行
        return self.fontMetrics().lineSpacing() * 2 + self._chrome()

    def _content_h(self, vw: int) -> int:
        """按当前宽度量全文高度（含自动折行），不依赖自身可见排版。"""
        text = self.toPlainText()
        self._meas.setDefaultFont(self.font())
        self._meas.setDocumentMargin(0)
        self._meas.setPlainText(text if text else "")
        self._meas.setTextWidth(float(vw))
        h = float(self._meas.size().height())
        if text:
            n = text.count("\n") + 1
            h = max(h, float(n * self.fontMetrics().lineSpacing()))
        return int(h + 0.5)

    def needed_height(self) -> int:
        vw = self.viewport().width()
        if vw <= 0:
            return self._min_h()
        line = self.fontMetrics().lineSpacing()
        # content+chrome 会多出约 2 行空白；减一行，底下只空 1 行
        h = self._content_h(vw) + self._chrome() - line
        return max(self._min_h(), h)

    def apply_height(self, h: int):
        if self.minimumHeight() == h and self.maximumHeight() == h:
            return
        self._fitting = True
        try:
            self.setFixedHeight(h)
        finally:
            self._fitting = False

    def _sync_pair(self):
        h = self.needed_height()
        other = self._pair
        if other is not None:
            h = max(h, other.needed_height())
            other.apply_height(h)
        self.apply_height(h)

    def _fit_height(self, *_):
        if self._fitting:
            return
        self._sync_pair()
        cb = getattr(self, "_after_fit", None)
        if callable(cb):
            cb()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not self._fitting:
            self._fit_height()

    def showEvent(self, e):
        super().showEvent(e)
        self._fit_height()

    def wheelEvent(self, e):
        e.ignore()


class _DragPreview(QWidget):
    """半透明拖拽影：跟鼠标走。原卡仍留在槽里，避免整条缩短。"""

    def __init__(self, pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self.setObjectName("PromptOrderDragPreview")
        self._pm = QPixmap(pixmap)
        self.setFixedSize(max(1, self._pm.width()), max(1, self._pm.height()))
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_StyledBackground, False)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setOpacity(0.55)
        p.drawPixmap(0, 0, self._pm)


class _DropGuide(QWidget):
    """拖拽插入点：竖向白色虚线，落在两张卡之间或最前/最后。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PromptOrderGhost")
        self.setFixedSize(_ORDER_GUIDE_W, _ORDER_CARD_H)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_StyledBackground, True)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        color = QColor("#ffffff") if theme.is_dark else QColor("#334155")
        pen = QPen(color, 2, Qt.CustomDashLine)
        pen.setDashPattern([3, 3])
        pen.setCapStyle(Qt.FlatCap)
        p.setPen(pen)
        x = self.width() // 2
        p.drawLine(x, 3, x, self.height() - 3)


class _HScrollArea(QScrollArea):
    """竖向滚轮改成横滑，方便翻分区卡。"""

    def wheelEvent(self, e):
        bar = self.horizontalScrollBar()
        delta = e.angleDelta().x() or e.angleDelta().y()
        if bar is not None and bar.maximum() > 0 and delta:
            bar.setValue(bar.value() - delta)
            e.accept()
            return
        super().wheelEvent(e)


_ORDER_CARD_BD = 2  # 未选中边粗；选中白边再加 2px。外框尺寸固定，不挤旁边的卡


class _OrderCard(QFrame):
    """分区顺序表里的小卡片，按住拖动改顺序。"""

    def hasHeightForWidth(self):
        return False

    def sizeHint(self):
        return QSize(_order_card_w(), _ORDER_CARD_H)

    def minimumSizeHint(self):
        return self.sizeHint()

    def __init__(self, key: str, title: str, owner: "_PromptSheet"):
        super().__init__(owner)
        self._key = key
        self._owner = owner
        self._press = None
        self._dragging = False
        self._editing = False
        self.setObjectName("PromptOrderCard")
        self.setFrameShape(QFrame.NoFrame)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAutoFillBackground(False)
        self.setCursor(Qt.OpenHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedSize(_order_card_w(), _ORDER_CARD_H)
        self._raw_title = title
        lab = QLabel()
        lab.setObjectName("PromptOrderCardLab")
        lab.setFont(_order_card_font())
        lab.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        lab.setWordWrap(True)
        lab.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Maximum)
        lab.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._lab = lab
        edit = QPlainTextEdit()
        edit.setObjectName("PromptOrderCardEdit")
        edit.setTabChangesFocus(True)
        edit.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        edit.setVisible(False)
        edit.installEventFilter(self)
        self._edit = edit
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(0)
        lay.addWidget(lab, 0, Qt.AlignTop | Qt.AlignHCenter)
        lay.addWidget(edit, 1)
        self.btn_freeze = QPushButton(self)
        self.btn_freeze.setObjectName("PromptOrderFreeze")
        self.btn_freeze.setCursor(Qt.PointingHandCursor)
        self.btn_freeze.setFocusPolicy(Qt.NoFocus)
        self.btn_freeze.setFixedSize(_TITLE_ACT, _TITLE_ACT)
        self.btn_freeze.setIconSize(QSize(_PASTE_ICON, _PASTE_ICON))
        self.btn_freeze.setToolTip("")
        self.btn_freeze.clicked.connect(
            lambda _=False, k=key: self._owner._toggle_freeze_field(k)
        )
        self._place_freeze()

    def _place_freeze(self):
        btn = getattr(self, "btn_freeze", None)
        if btn is None:
            return
        m = 4
        btn.setGeometry(
            (self.width() - _TITLE_ACT) // 2,
            self.height() - _TITLE_ACT - m,
            _TITLE_ACT,
            _TITLE_ACT,
        )
        btn.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_freeze()

    def apply_title(self):
        self._lab.setFont(_order_card_font())
        self._lab.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        fm = QFontMetrics(_order_card_font())
        inner_w = _order_card_line_width()
        extra = (_TITLE_ACT + 4) if self.btn_freeze.isVisible() else 0
        max_lines = max(1, (_ORDER_CARD_H - 8 - extra) // max(1, fm.lineSpacing()))
        self._lab.setText(_fit_card_text(self._raw_title, fm, inner_w, max_lines))

    def set_title(self, title: str):
        self._raw_title = title
        self.apply_title()

    def is_editing(self) -> bool:
        return self._editing

    def begin_rename(self):
        if self._editing:
            self._edit.setFocus()
            self._place_rename_cursor()
            return
        self._editing = True
        self._lab.hide()
        self.btn_freeze.hide()
        self._edit.setPlainText(self._raw_title)
        self._edit.show()
        self._edit.setFocus()
        self._place_rename_cursor()
        self.setCursor(Qt.IBeamCursor)

    def _place_rename_cursor(self):
        """光标放末尾，保留原名以便改几个字。"""
        cur = self._edit.textCursor()
        cur.movePosition(QTextCursor.End)
        self._edit.setTextCursor(cur)

    def finish_rename(self, commit: bool):
        if not self._editing:
            return None
        text = (self._edit.toPlainText() or "").strip()
        self._editing = False
        self._edit.hide()
        self._lab.show()
        self.setCursor(Qt.OpenHandCursor)
        if commit and text:
            return text
        return None

    def eventFilter(self, obj, ev):
        if obj is self._edit:
            t = ev.type()
            if t == QEvent.KeyPress:
                key = ev.key()
                if key in (Qt.Key_Return, Qt.Key_Enter):
                    self._owner._commit_order_rename()
                    return True
                if key == Qt.Key_Escape:
                    self._owner._cancel_order_rename()
                    return True
            if t == QEvent.FocusOut:
                QTimer.singleShot(0, self._owner._commit_order_rename)
        return super().eventFilter(obj, ev)

    def mousePressEvent(self, e):
        if self._editing:
            return
        if e.button() == Qt.LeftButton:
            self._owner._commit_order_rename()
            btn = getattr(self, "btn_freeze", None)
            if btn is not None and btn.isVisible() and btn.geometry().contains(e.pos()):
                # 点在冻结钮上：不当成拖拽。按钮在上层走 clicked；
                # 被标题挡住时这里补一刀。
                self._press = None
                self._dragging = False
                if self.childAt(e.pos()) is not btn:
                    self._owner._toggle_freeze_field(self._key)
                e.accept()
                return
            self._press = e.globalPos()
            self._dragging = False
            self._owner._select_order_card(self._key)
        e.accept()

    def mouseMoveEvent(self, e):
        if self._editing:
            return
        if self._press is None or not (e.buttons() & Qt.LeftButton):
            return
        if not self._dragging:
            if (e.globalPos() - self._press).manhattanLength() < _ORDER_DRAG_PX:
                return
            self._dragging = True
            self.setCursor(Qt.ClosedHandCursor)
        self._owner._drag_order_card(self._key, e.globalPos())

    def mouseReleaseEvent(self, e):
        if self._dragging:
            self._owner._end_order_drag(self._key)
        self._dragging = False
        self._press = None
        self.setCursor(Qt.OpenHandCursor)
        super().mouseReleaseEvent(e)


class _AddPresetDialog(QDialog):
    """标题 + 内容，给右侧「+」添加自定义预设。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加预设")
        self.setModal(True)
        self.setMinimumWidth(420)
        self._name = QLineEdit()
        self._name.setPlaceholderText("标题，如 东亚马尾")
        self._value = QPlainTextEdit()
        self._value.setPlaceholderText("点选后写入左侧的内容")
        self._value.setFixedHeight(96)
        form = QFormLayout()
        form.addRow("标题", self._name)
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


class _FreezeVeil(QWidget):
    """盖在编辑框+预设上的黑罩：冻结时整区不可点。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PromptFreezeVeil")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.ForbiddenCursor)
        self.setVisible(False)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 235))
        p.drawRoundedRect(QRectF(self.rect()), 4, 4)
        p.setPen(QColor("#f1f5f9"))
        f = QFont()
        f.setPixelSize(13)
        f.setBold(True)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, "已冻结")


class _PlusButton(QPushButton):
    """预设末尾的「+」：背景 / 边框照常走 QSS，加号自己画两条线，按控件几何中心居中。

    用字形「+」时，Qt 是按字体行框（ascent+descent）垂直居中的；微软雅黑下降部较大，
    「+」墨迹贴着基线，结果整体偏下约 1px，分数缩放下还会左右差半像素。
    """

    def __init__(self, parent=None):
        super().__init__("", parent)
        self._plus_colors: Dict[str, str] = {}
        self.setAccessibleName("+")

    def set_plus_colors(self, normal: str, hover: str, press: str, disabled: str):
        self._plus_colors = {
            "normal": normal, "hover": hover, "press": press, "disabled": disabled,
        }
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self.isEnabled():
            state = "disabled"
        elif self.isDown():
            state = "press"
        elif self.underMouse():
            state = "hover"
        else:
            state = "normal"
        col = QColor(self._plus_colors.get(state) or self._plus_colors.get("normal") or "#94a3b8")
        w, h = float(self.width()), float(self.height())
        side = min(w, h)
        pen_w = max(2.0, float(round(side / 12.0)))  # 24~26px 时 2px，约等于 16px 粗体加号笔画
        half = float(round(side * 0.16))             # 26px → 半臂 4px，整长 8px（与原 16px 粗体字形同大）
        cx, cy = w / 2.0, h / 2.0
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            pen = QPen(col)
            pen.setWidthF(pen_w)
            pen.setCapStyle(Qt.FlatCap)
            p.setPen(pen)
            p.drawLine(QPointF(cx - half, cy), QPointF(cx + half, cy))
            p.drawLine(QPointF(cx, cy - half), QPointF(cx, cy + half))
        finally:
            p.end()


class _PromptSection(QWidget):
    """一行：竖排标题 + 40% 内容 + 60% 右侧预设。"""

    def __init__(self, owner: "_PromptSheet", key: str, parent=None):
        super().__init__(parent)
        self._owner = owner
        self._key = key

    def resizeEvent(self, event):
        super().resizeEvent(event)
        owner = getattr(self, "_owner", None)
        if owner is not None:
            owner._apply_section_widths(self._key)


class _PromptSheet(QWidget):
    """一个分页里的提示词编辑器。正文和自定义预设只属于这一页。"""

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
        self.setObjectName("PageRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._page_id = ""
        self._loading = False
        self._prefs_dirty_cb = None
        self._left: Dict[str, QPlainTextEdit] = {}
        self._right: Dict[str, QPlainTextEdit] = {}
        self._labels: List[_VTitle] = []
        self._title_bars: List[QFrame] = []
        self._title_bar_by_key: Dict[str, QFrame] = {}
        self._right_slots: Dict[str, QWidget] = {}
        self._xfer_btns: List[QPushButton] = []
        self._paste_btns: List[QPushButton] = []
        self._specs: List[Tuple[str, str, str]] = list(_FIELDS)
        self._sections: Dict[str, QWidget] = {}
        self._order_cards: Dict[str, _OrderCard] = {}
        self._title_labs: Dict[str, _VTitle] = {}
        self._frozen_keys = set()
        self._freeze_btns: Dict[str, QPushButton] = {}
        self._freeze_veils: Dict[str, QWidget] = {}
        self._preset_panes: Dict[str, QFrame] = {}
        self._preset_flows: Dict[str, FlowLayout] = {}
        self._chip_groups: Dict[str, QButtonGroup] = {}
        self._chip_btns: List[QPushButton] = []
        self._add_btns: List[QPushButton] = []
        self._preset_paste_btns: List[QPushButton] = []
        self._custom_presets: Dict[str, List[dict]] = {}
        # H3 预设：展开状态由容器共享（落 prompt_editor.h3_expanded）；按钮首次展开才建
        self._h3_expanded: Dict[str, bool] = {}
        self._entry_btns: Dict[str, Dict[str, QPushButton]] = {}
        self._field_fixed: Dict[str, tuple] = {}
        self._entry_sig: Dict[str, tuple] = {}
        self._preset_mru: Dict[str, List[str]] = {}
        self._h3_toggles: Dict[str, QPushButton] = {}
        self._h3_flows: Dict[str, FlowLayout] = {}
        self._selected_key = None
        self._order_dragging = False
        self._drag_key = None
        self._drag_ghost = None
        self._drag_grab = None
        self._drag_preview = None
        self._drag_insert = None
        self._card_anim = None
        self._hist: List[dict] = []
        self._redo_stack: List[dict] = []
        self._hist_head = None
        self._hist_applying = False

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._persist_now)

        scroll = QScrollArea(self)
        scroll.setObjectName("PromptScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setAttribute(Qt.WA_StyledBackground, True)
        scroll.setStyleSheet(
            "QScrollArea#PromptScroll{background:transparent;border:none;}"
            "QScrollArea#PromptScroll > QWidget > QWidget{background:transparent;}"
        )
        try:
            scroll.viewport().setAutoFillBackground(False)
            scroll.viewport().setStyleSheet("background:transparent;")
        except Exception:
            pass

        host = QWidget()
        host.setObjectName("PromptScrollHost")
        host.setAttribute(Qt.WA_StyledBackground, True)
        host.setStyleSheet("#PromptScrollHost{background:transparent;border:none;}")
        scroll.setWidget(host)

        root = QVBoxLayout(host)
        root.setContentsMargins(0, 0, 0, 8)
        root.setSpacing(10)

        sec_host = QWidget()
        sec_host.setObjectName("PromptSecHost")
        sec_host.setAttribute(Qt.WA_StyledBackground, True)
        sec_host.setStyleSheet("#PromptSecHost{background:transparent;border:none;}")
        self._sec_lay = QVBoxLayout(sec_host)
        self._sec_lay.setContentsMargins(0, 0, 0, 0)
        self._sec_lay.setSpacing(10)
        for key, label, placeholder in self._specs:
            self._add_section_widget(key, label, placeholder)
        root.addWidget(sec_host)
        root.addWidget(self._make_full_prompt_section())

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        foot.setSpacing(8)
        left_foot = QWidget()
        left_foot.setAttribute(Qt.WA_StyledBackground, True)
        left_foot.setStyleSheet("background:transparent;border:none;")
        left_lay = QHBoxLayout(left_foot)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(6)
        self.btn_field_add = self._make_order_action_btn(
            "+", "添加分区（未用的预设，或自定义）"
        )
        self.btn_field_del = self._make_order_action_btn(
            "-", "删除选中的分区"
        )
        self.btn_field_add.clicked.connect(self._add_field_clicked)
        self.btn_field_del.clicked.connect(self._delete_selected_field)
        for btn in (self.btn_field_add, self.btn_field_del):
            btn.setMinimumWidth(0)
            btn.setFixedSize(_COPY_H, _COPY_H)
            btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self._style_field_square_btn(btn)
        left_lay.addWidget(self.btn_field_add, 0)
        left_lay.addWidget(self.btn_field_del, 0)
        left_lay.addStretch(1)
        right_foot = QWidget()
        right_foot.setAttribute(Qt.WA_StyledBackground, True)
        right_foot.setStyleSheet("background:transparent;border:none;")
        right_lay = QHBoxLayout(right_foot)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(6)
        right_lay.addWidget(self._make_copy_all_btn("orig"), 1)
        hist_mid = QWidget()
        hist_mid.setAttribute(Qt.WA_StyledBackground, True)
        hist_mid.setStyleSheet("background:transparent;border:none;")
        hist_row = QHBoxLayout(hist_mid)
        hist_row.setContentsMargins(0, 0, 0, 0)
        hist_row.setSpacing(4)
        self.btn_undo = self._make_hist_btn("undo")
        self.lbl_hist_count = self._make_hist_count()
        self.btn_redo = self._make_hist_btn("redo")
        self.btn_undo.clicked.connect(self._undo)
        self.btn_redo.clicked.connect(self._redo)
        hist_row.addWidget(self.btn_undo, 0)
        hist_row.addWidget(self.lbl_hist_count, 0)
        hist_row.addWidget(self.btn_redo, 0)
        right_lay.addWidget(hist_mid, 0)
        self._refresh_hist_btns()
        foot.addWidget(left_foot, 1)
        foot.addWidget(right_foot, 1)
        foot_wrap = QWidget()
        foot_wrap.setObjectName("PromptFoot")
        foot_wrap.setAttribute(Qt.WA_StyledBackground, True)
        foot_wrap.setStyleSheet("#PromptFoot{background:transparent;border:none;}")
        foot_wrap.setLayout(foot)
        order_box = self._make_order_box()
        self._rebuild_order_cards()
        self._scroll = scroll
        self._foot_wrap = foot_wrap

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        outer.addWidget(scroll, 1)
        outer.addWidget(order_box, 0)
        outer.addWidget(foot_wrap, 0)

        try:
            scroll.verticalScrollBar().rangeChanged.connect(self._sync_foot_align)
        except Exception:
            pass
        QTimer.singleShot(0, self._sync_foot_align)

        try:
            theme.changed.connect(self.restyle_theme)
        except Exception:
            pass
        self._hist_head = self._snapshot()
        self._click_filter_on = False
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            self._click_filter_on = True
        try:
            self.destroyed.connect(self._teardown_click_filter)
        except Exception:
            pass
        self._clip_on = False
        clip = QApplication.clipboard()
        if clip is not None:
            try:
                clip.dataChanged.connect(self._refresh_preset_paste_btns)
                self._clip_on = True
            except Exception:
                pass
        self._clip_timer = QTimer(self)
        self._clip_timer.setInterval(500)
        self._clip_timer.timeout.connect(self._refresh_preset_paste_btns)
        self._clip_timer.start()

    @staticmethod
    def _make_pair_row(left, mid, right, align_top: bool = False) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        if align_top:
            row.setAlignment(Qt.AlignTop)
        row.addWidget(left, 1)
        if mid is None:
            row.addSpacing(_XFER_W)
        else:
            row.addWidget(mid, 0)
        row.addWidget(right, 1)
        return row

    def _set_vtitle(self, lab: _VTitle, title: str):
        lab.set_title(title)

    def _make_field_bar(self, key: str, title: str):
        bar = QFrame()
        bar.setObjectName("PromptFieldBar")
        bar.setProperty("fieldKey", key)
        bar.setFrameShape(QFrame.NoFrame)
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setAutoFillBackground(True)
        bar.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        bar_lay = QVBoxLayout(bar)
        bar_lay.setContentsMargins(_TITLE_BAR_PAD_H, 3, _TITLE_BAR_PAD_H, 3)
        bar_lay.setSpacing(_TITLE_COL_GAP)
        freeze_btn = self._make_freeze_btn(key)
        self._freeze_btns[key] = freeze_btn
        lab = _VTitle()
        self._set_vtitle(lab, title)
        bar_lay.addWidget(freeze_btn, 0, Qt.AlignHCenter | Qt.AlignTop)
        bar_lay.addWidget(lab, 0, Qt.AlignHCenter | Qt.AlignTop)
        bar_lay.addStretch(1)
        self._style_field_bar(bar, lab)
        self._style_freeze_btn(freeze_btn)
        return bar, lab

    def _add_section_widget(self, key: str, title: str, placeholder: str) -> QWidget:
        wrap = _PromptSection(self, key)
        wrap.setObjectName(f"PromptSection_{key}")
        wrap.setAttribute(Qt.WA_StyledBackground, True)
        wrap.setStyleSheet("background:transparent;border:none;")
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.setAlignment(Qt.AlignTop)
        bar, lab = self._make_field_bar(key, title)
        self._title_bars.append(bar)
        self._labels.append(lab)
        self._title_labs[key] = lab
        self._title_bar_by_key[key] = bar
        left = self._make_edit("blue", key, placeholder)
        left.normalize_paste = True
        left.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        left._after_fit = lambda k=key: self._sync_section_row(k)
        left.textChanged.connect(lambda *_ , k=key: self._sync_field_chips(k))
        right = self._make_edit("green", key, placeholder)
        right.hide()
        self._left[key] = left
        self._right[key] = right
        slot = QWidget()
        slot.setObjectName(f"PromptRightSlot_{key}")
        slot.setAttribute(Qt.WA_StyledBackground, True)
        slot.setStyleSheet("background:transparent;border:none;")
        slot.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._right_slots[key] = slot
        self._fill_char_presets(key, slot)
        row.addWidget(bar, 0)
        row.addWidget(left, 0)
        row.addWidget(slot, 0)
        right.setParent(wrap)
        veil = _FreezeVeil(wrap)
        self._freeze_veils[key] = veil
        self._sections[key] = wrap
        self._sec_lay.addWidget(wrap)
        return wrap

    def _char_preset_pairs(self, src_keys: Tuple[str, ...]) -> List[Tuple[str, str, str]]:
        """(角色模板key, 显示名, 写入值)。"""
        out: List[Tuple[str, str, str]] = []
        for src in src_keys:
            if src == "camera":
                val = (_CAMERA_DEFAULT or "").strip()
                if val:
                    out.append((src, "牛仔镜头", val))
                continue
            for name, value in _CHAR_PRESETS.get(src) or []:
                n = str(name or "").strip()
                v = str(value or "").strip()
                if n and v:
                    out.append((src, n, v))
        return out

    def _fill_char_presets(self, key: str, slot: QWidget):
        pane = QFrame()
        pane.setObjectName(f"PromptPresetPane_{key}")
        pane.setFrameShape(QFrame.NoFrame)
        pane.setAttribute(Qt.WA_StyledBackground, True)
        pane.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        col = QVBoxLayout(pane)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        host = QWidget()
        host.setAttribute(Qt.WA_StyledBackground, True)
        host.setStyleSheet("background:transparent;border:none;")
        host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        flow = FlowLayout(host, 0, 6, 6)
        col.addWidget(host, 0)
        # 第 16 条起单独一块 H3 区（另起一行、虚线分隔），只在展开时显示；
        # 前 15 条 /「+」/「H3」开关留在上面的流里，两块按序号重新分配
        h3_host = QWidget()
        h3_host.setObjectName(f"PromptH3Area_{key}")
        h3_host.setAttribute(Qt.WA_StyledBackground, True)
        h3_host.setStyleSheet(
            f"QWidget#PromptH3Area_{key}{{background:transparent;border:none;"
            f"border-top:1px dashed rgba(148,163,184,110);}}"
        )
        h3_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        h3_flow = FlowLayout(h3_host, 0, 6, 6)
        h3_flow.setContentsMargins(0, 6, 0, 0)
        h3_host.hide()
        col.addWidget(h3_host, 0)
        col.addStretch(1)
        self._h3_flows[key] = h3_flow
        slot_lay = QVBoxLayout(slot)
        slot_lay.setContentsMargins(0, 0, 0, 0)
        slot_lay.setSpacing(0)
        slot_lay.addWidget(pane)
        self._preset_panes[key] = pane
        self._preset_flows[key] = flow
        self._style_preset_pane(pane)
        group = QButtonGroup(self)
        group.setExclusive(False)
        self._chip_groups[key] = group
        self._rebuild_field_chips(key)

    def _rebuild_field_chips(self, key: str):
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
        # 预设条目按钮（内置 + H3）和「H3」开关只建一次：重建时只从流里摘下，稍后按顺序放回
        entry_btns = self._entry_btns.setdefault(key, {})
        keep_ids = {id(b) for b in entry_btns.values()}
        h3_toggle = self._h3_toggles.get(key)
        if h3_toggle is not None:
            keep_ids.add(id(h3_toggle))
        i = 0
        while i < flow.count():
            item = flow.itemAt(i)
            w = item.widget() if item is not None else None
            if w is not None and id(w) in keep_ids:
                i += 1  # 留在流里（布局项不摘），稍后 _reorder_flow 只调顺序
                continue
            flow.takeAt(i)
            if w is None:
                continue
            self._chip_btns = [b for b in self._chip_btns if b is not w]
            self._add_btns = [b for b in self._add_btns if b is not w]
            self._preset_paste_btns = [b for b in self._preset_paste_btns if b is not w]
            w.setParent(None)
            w.deleteLater()
        for btn in entry_btns.values():
            group.addButton(btn)
        paste_btn = self._make_preset_paste_btn(key)
        flow.addWidget(paste_btn)
        self._preset_paste_btns.append(paste_btn)
        # 自定义预设已并入 _ordered_entries（参与 MRU / 前 15 / H3），这里不再单独建
        customs: List[QPushButton] = []
        add_btn = self._make_add_preset_btn(key)
        flow.addWidget(add_btn)
        self._add_btns.append(add_btn)
        self._field_fixed[key] = (paste_btn, customs, add_btn)
        self._entry_sig.pop(key, None)
        self._layout_field_entries(key)
        self._sync_field_chips(key)
        self._refresh_preset_paste_btns()

    def _builtin_pairs(self, key: str) -> List[Tuple[str, str, str]]:
        """内置预设：优先根目录 txt，读不到再用代码里的 _CHAR_PRESETS。"""
        builtin, _h3 = _txt_presets()
        if builtin is not None:
            return [("builtin", n, v) for n, v in (builtin.get(key) or [])]
        return self._char_preset_pairs(_CHAR_PRESET_KEYS.get(key) or ())

    @staticmethod
    def _h3_entries(key: str) -> List[Tuple[str, str]]:
        _builtin, h3 = _txt_presets()
        return list(h3.get(key) or [])

    def _field_chip_values(self, key: str) -> List[str]:
        """这一栏所有预设内容（内置 + 自定义 + H3，H3 未展开也算），用于长条优先判定。"""
        vals = [v for _s, _n, v in self._builtin_pairs(key)]
        for it in self._custom_presets.get(key) or []:
            v = str((it or {}).get("value") or "").strip()
            if v:
                vals.append(v)
        vals.extend(v for _n, v in self._h3_entries(key))
        return vals

    def _active_values_for(self, key: str) -> set:
        ed = self._left.get(key)
        text = (ed.toPlainText() if ed is not None else "") or ""
        if not text.strip():
            return set()
        return _active_chip_values(text, self._field_chip_values(key))

    def _ordered_entries(self, key: str) -> List[Tuple[str, str, str]]:
        """(来源, 标题, 内容)：内置在前、H3 在后、按内容去重；再按共享 MRU 把最近加过的提到最前。

        MRU 里 txt 已没有的内容忽略；不在 MRU 里的条目保持 txt 原顺序排在后面。
        """
        entries: List[Tuple[str, str, str]] = []
        seen = set()
        # 默认顺序：本栏自定义预设 → 内置 → H3（同内容只留第一个，即自定义优先）
        for it in self._custom_presets.get(key) or []:
            name = str((it or {}).get("name") or "").strip()
            value = str((it or {}).get("value") or "").strip()
            if name and value and value not in seen:
                seen.add(value)
                entries.append(("custom", name, value))
        for src, name, value in self._builtin_pairs(key):
            if value and value not in seen:
                seen.add(value)
                entries.append((src, name, value))
        for name, value in self._h3_entries(key):
            if value and value not in seen:
                seen.add(value)
                entries.append(("h3", name, value))
        mru = (getattr(self, "_preset_mru", None) or {}).get(key) or []
        if mru and entries:
            pos = {e[2]: i for i, e in enumerate(entries)}
            front = []
            used = set()
            for v in mru:
                i = pos.get(v)
                if i is None or v in used:
                    continue
                used.add(v)
                front.append(entries[i])
            if front:
                entries = front + [e for e in entries if e[2] not in used]
        return entries

    def _h3_is_expanded(self, key: str) -> bool:
        return bool((getattr(self, "_h3_expanded", None) or {}).get(key))

    @staticmethod
    def _flow_take(flow: FlowLayout, w: QWidget) -> bool:
        """只把 w 的布局项从流里摘掉（控件本身不动）。"""
        for i in range(flow.count()):
            it = flow.itemAt(i)
            if it is not None and it.widget() is w:
                flow.takeAt(i)
                return True
        return False

    @classmethod
    def _reorder_flow(cls, flow: FlowLayout, order: List[QWidget], others=()):
        """把流里的控件按 order 排好；缺的先从 others（同一栏的另一块流）里摘出来再加进来。

        只调换布局项顺序 / 搬家，不删不重建按钮。
        """
        present = {}
        for i in range(flow.count()):
            it = flow.itemAt(i)
            w = it.widget() if it is not None else None
            if w is not None:
                present[id(w)] = it
        other_ids = {}
        for of in others or ():
            if of is None or of is flow:
                continue
            for i in range(of.count()):
                it = of.itemAt(i)
                w = it.widget() if it is not None else None
                if w is not None:
                    other_ids[id(w)] = of
        for w in order:
            if id(w) not in present:
                src = other_ids.pop(id(w), None)
                if src is not None:
                    cls._flow_take(src, w)
                    src.invalidate()
                flow.addWidget(w)
                present[id(w)] = flow.itemAt(flow.count() - 1)
        items = getattr(flow, "_items", None)
        ordered = [present[id(w)] for w in order]
        if isinstance(items, list):
            ids = {id(it) for it in ordered}
            items[:] = ordered + [it for it in items if id(it) not in ids]
            flow.invalidate()
            return
        widgets = []
        while flow.count():
            it = flow.takeAt(0)
            w = it.widget() if it is not None else None
            if w is not None:
                widgets.append(w)
        known = {id(w) for w in order}
        for w in list(order) + [w for w in widgets if id(w) not in known]:
            flow.addWidget(w)

    def _layout_field_entries(self, key: str, force: bool = False) -> bool:
        """按当前顺序摆放：上面的流放粘贴、前 _TOP_N 条、「+」、「H3 ▸/▾」；第 _TOP_N+1 条起放下面的 H3 区。

        前 _TOP_N 条总是建好并显示；其余只有展开（且页面可见或 force）时才建。返回是否新建了按钮。
        """
        flow = self._preset_flows.get(key)
        group = self._chip_groups.get(key)
        fixed = self._field_fixed.get(key)
        if flow is None or group is None or fixed is None:
            return False
        paste_btn, customs, add_btn = fixed
        entries = self._ordered_entries(key)
        top, rest = entries[:_TOP_N], entries[_TOP_N:]
        expanded = bool(rest) and self._h3_is_expanded(key)
        build_rest = expanded and (force or self.isVisible())
        btns = self._entry_btns.setdefault(key, {})
        want_kind = {e[2]: (e[0] == "custom", e[1]) for e in entries}
        for value in list(btns.keys()):
            b = btns[value]
            kind = want_kind.get(value)
            try:
                same = kind is not None and (
                    bool(b.property("chipCustom")) == kind[0]
                    and str(b.property("chipName") or "") == kind[1]
                )
            except RuntimeError:
                same = False
            if not same:
                # 自定义预设被删 / 同内容换了来源：摘掉旧按钮，需要时下面重建
                btns.pop(value, None)
                self._drop_entry_btn(flow, group, b)
        new_btns: List[QPushButton] = []
        for src, name, value in top + (rest if build_rest else []):
            if value not in btns:
                b = self._make_preset_chip(key, src, name, value, custom=(src == "custom"))
                btns[value] = b
                group.addButton(b)
                new_btns.append(b)
        if new_btns:
            self._chip_btns.extend(new_btns)
        toggle = self._h3_toggles.get(key)
        if rest and toggle is None:
            toggle = self._make_h3_toggle(key)
            self._h3_toggles[key] = toggle
        if toggle is not None:
            toggle.setText("H3 ▾" if expanded else "H3 ▸")
        order: List[QWidget] = [paste_btn] + [btns[e[2]] for e in top] + list(customs) + [add_btn]
        if rest:
            order.append(toggle)
        rest_btns = [btns[e[2]] for e in rest if e[2] in btns]
        h3_flow = self._h3_flows.get(key)
        if h3_flow is None:
            # 没有独立 H3 区（理论上不会）：退回单流
            order += rest_btns
            rest_btns = []
        host = flow.parentWidget()
        h3_host = h3_flow.parentWidget() if h3_flow is not None else None
        hosts = [h for h in (host, h3_host) if h is not None and h.isVisible()]
        batch = len(new_btns) > 3 and bool(hosts)
        if batch:
            for h in hosts:
                h.hide()  # 一次新建很多按钮时先藏父控件，避免逐个触发整流重排
        try:
            # 每次都按序号重新分配：前 _TOP_N 条进上面的流，其余进 H3 区（互相搬家）
            self._reorder_flow(flow, order, (h3_flow,))
            if h3_flow is not None:
                self._reorder_flow(h3_flow, rest_btns, (flow,))
            want = []
            for e in top:
                want.append((btns[e[2]], True))
            for b in rest_btns:
                want.append((b, True))  # H3 区里的按钮本身常显，靠 H3 区整体显隐
            if h3_flow is None:
                for e in rest:
                    b = btns.get(e[2])
                    if b is not None:
                        want.append((b, expanded))
            if toggle is not None:
                want.append((toggle, bool(rest)))
            todo = [(b, vis) for b, vis in want if b.isHidden() == vis]
            inner = (not batch) and host is not None and host.isVisible() and len(todo) > 3
            if inner:
                host.hide()
            try:
                for b, vis in todo:
                    b.setHidden(not vis)
            finally:
                if inner:
                    host.show()
        finally:
            if batch:
                for h in hosts:
                    h.show()
        if h3_host is not None:
            show_h3 = expanded and bool(rest_btns)
            if h3_host.isHidden() == show_h3:
                h3_host.setVisible(show_h3)
        self._entry_sig[key] = (
            self._entries_sig(entries), expanded, bool(build_rest or not expanded),
        )
        return bool(new_btns)

    @staticmethod
    def _entries_sig(entries) -> tuple:
        return tuple((e[0] == "custom", e[1], e[2]) for e in entries)

    def _drop_entry_btn(self, flow: FlowLayout, group: QButtonGroup, b: QPushButton):
        try:
            group.removeButton(b)
        except RuntimeError:
            pass
        self._chip_btns = [x for x in self._chip_btns if x is not b]
        try:
            pk = str(b.property("promptKey") or "")
        except RuntimeError:
            pk = ""
        for fl in (flow, self._h3_flows.get(pk)):
            if fl is not None and self._flow_take(fl, b):
                fl.invalidate()
                break
        try:
            b.setParent(None)
            b.deleteLater()
        except RuntimeError:
            pass

    def _make_h3_toggle(self, key: str) -> QPushButton:
        btn = QPushButton("H3 ▸")
        btn.setObjectName(f"PromptH3Toggle_{key}")
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedHeight(24)
        btn.setProperty("presetKey", key)
        btn.setProperty("chipCustom", False)
        self._style_h3_toggle(btn)
        btn.clicked.connect(lambda _=False, k=key: self._toggle_h3(k))
        return btn

    def _style_h3_toggle(self, btn: QPushButton):
        # 与预设按钮同一套样式（不可勾选，所以不会变绿）
        self._style_chip(btn)

    def _notify_prefs_only(self, what: str):
        """只通知落盘（展开状态 / MRU 顺序），不进撤销历史。"""
        if self._loading:
            return
        cb = getattr(self, "_prefs_dirty_cb", None)
        if callable(cb):
            try:
                cb()
            except Exception:
                log.exception("保存%s失败", what)

    def _after_entry_layout(self, key: str, built: bool):
        if built:
            self._sync_field_chips(key)
        self._sync_section_row(key)
        QTimer.singleShot(0, lambda k=key: self._sync_section_row(k))

    def _toggle_h3(self, key: str):
        if len(self._ordered_entries(key)) <= _TOP_N:
            return
        if self._h3_is_expanded(key):
            self._h3_expanded.pop(key, None)
        else:
            self._h3_expanded[key] = True
        built = self._layout_field_entries(key, force=True)
        self._after_entry_layout(key, built)
        self._notify_prefs_only(" H3 展开状态")

    def _bump_mru(self, key: str, value: str):
        """加进正文的内置 / H3 条目提到本栏最前（各分页共用一份顺序）。"""
        value = (value or "").strip()
        mru = getattr(self, "_preset_mru", None)
        if not value or mru is None:
            return
        cur = list(mru.get(key) or [])
        if cur and cur[0] == value:
            return  # 已在最前
        mru[key] = ([value] + [v for v in cur if v != value])[:_MRU_MAX]
        built = self._layout_field_entries(key, force=True)
        self._after_entry_layout(key, built)
        self._notify_prefs_only("预设顺序")

    def _sync_h3_all(self):
        """进入页面时按共享的展开状态 / MRU 顺序补建、重排（别的分页可能改过）。"""
        for key in list(self._preset_flows.keys()):
            if key not in self._field_fixed:
                continue
            entries = self._ordered_entries(key)
            expanded = len(entries) > _TOP_N and self._h3_is_expanded(key)
            sig = (self._entries_sig(entries), expanded, True)
            if self._entry_sig.get(key) == sig:
                continue
            built = self._layout_field_entries(key, force=True)
            self._after_entry_layout(key, built)
    def _make_preset_chip(
        self, prompt_key: str, src_key: str, name: str, value: str, custom: bool = False,
    ) -> QPushButton:
        btn = QPushButton(name)
        btn.setCheckable(True)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedHeight(24)
        btn.setProperty("promptKey", prompt_key)
        btn.setProperty("srcKey", src_key)
        btn.setProperty("chipName", name)
        btn.setProperty("chipValue", value)
        btn.setProperty("chipCustom", bool(custom))
        self._style_chip(btn)
        self._refresh_chip_caption(btn, False)
        btn.clicked.connect(lambda _=False, b=btn: self._on_chip_clicked(b))
        if custom:
            btn.setContextMenuPolicy(Qt.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, b=btn: self._chip_context_menu(b, pos)
            )
        return btn

    def _make_add_preset_btn(self, key: str) -> QPushButton:
        btn = _PlusButton()
        btn.setObjectName(f"PromptPresetAdd_{key}")
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedSize(24, 24)
        btn.setProperty("presetKey", key)
        self._style_add_preset_btn(btn)
        btn.clicked.connect(lambda _=False, k=key: self._add_custom_preset(k))
        return btn

    def _make_preset_paste_btn(self, key: str) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName(f"PromptPresetPaste_{key}")
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setFixedSize(24, 24)
        btn.setIconSize(QSize(14, 14))
        btn.setProperty("presetKey", key)
        btn.setToolTip("")
        self._style_preset_paste_btn(btn)
        btn.clicked.connect(lambda _=False, k=key: self._paste_into_field(k))
        return btn

    def _make_freeze_btn(self, key: str) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName(f"PromptFreeze_{key}")
        btn.setProperty("fieldKey", key)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setFixedSize(_TITLE_ACT, _TITLE_ACT)
        btn.setIconSize(QSize(_PASTE_ICON, _PASTE_ICON))
        btn.clicked.connect(lambda _=False, k=key: self._toggle_freeze_field(k))
        return btn

    @staticmethod
    def _clipboard_has_text() -> bool:
        try:
            return bool((QApplication.clipboard().text() or "").strip())
        except Exception:
            return False

    def _refresh_preset_paste_btns(self):
        for btn in list(getattr(self, "_preset_paste_btns", []) or []):
            try:
                btn.objectName()
            except RuntimeError:
                continue
            self._style_preset_paste_btn(btn)

    def _style_preset_paste_btn(self, btn: QPushButton):
        key = str(btn.property("presetKey") or "")
        frozen = key in getattr(self, "_frozen_keys", set())
        has = (not frozen) and self._clipboard_has_text()
        btn.setEnabled(has)
        btn.setCursor(Qt.PointingHandCursor if has else Qt.ArrowCursor)
        h = self._hot_colors()
        accent = _field_accent(key)
        if has:
            bd, bg, ico = accent, _fade80(accent), accent
        else:
            if theme.is_dark:
                bd, bg = "#475569", "#1e293b"
            else:
                bd, bg = "#94a3b8", "#e2e8f0"
            ico = tk("text_dim")
        btn.setStyleSheet(
            f"QPushButton{{"
            f"min-width:24px;max-width:24px;min-height:24px;max-height:24px;"
            f"padding:0;border-radius:6px;"
            f"border:1px solid {bd};background:{bg};}}"
            f"QPushButton:hover{{"
            f"background:{h['hover_bg']};border-color:{h['hover_bd']};}}"
            f"QPushButton:pressed{{"
            f"background:{h['press_bg']};border-color:{h['press_bd']};}}"
            f"QPushButton:disabled{{"
            f"background:{bg};border-color:{bd};}}"
        )
        btn.setIcon(_clipboard_icon(14, ico))

    def _paste_into_field(self, key: str):
        if key in getattr(self, "_frozen_keys", set()):
            return
        if not self._clipboard_has_text():
            self._refresh_preset_paste_btns()
            return
        try:
            raw = QApplication.clipboard().text() or ""
        except Exception:
            raw = ""
        text = _normalize_prompt_paste(raw)
        if not text:
            return
        ed = self._left.get(key)
        if ed is None:
            return
        cur = ed.toPlainText() or ""
        if cur and not cur[-1].isspace():
            text = " " + text
        cursor = ed.textCursor()
        cursor.movePosition(QTextCursor.End)
        ed.setTextCursor(cursor)
        ed.insertPlainText(text)

    def _style_preset_pane(self, pane: QFrame):
        name = pane.objectName()
        pane.setStyleSheet(
            f"QFrame#{name}{{background:transparent;border:none;}}"
        )

    def _style_add_preset_btn(self, btn: QPushButton):
        h = self._hot_colors()
        if theme.is_dark:
            bd, bg, fg = "#64748b", "#1e293b", tk("text")
        else:
            bd, bg, fg = "#94a3b8", "#f1f5f9", tk("text")
        btn.setStyleSheet(
            f"QPushButton{{"
            f"min-width:24px;max-width:24px;min-height:24px;max-height:24px;"
            f"padding:0;border-radius:6px;"
            f"border:1px solid {bd};background:{bg};color:{fg};"
            f"font-size:16px;font-weight:700;}}"
            f"QPushButton:hover{{"
            f"background:{h['hover_bg']};border-color:{h['hover_bd']};color:{h['hover_fg']};}}"
            f"QPushButton:pressed{{"
            f"background:{h['press_bg']};border-color:{h['press_bd']};color:{h['press_fg']};}}"
        )
        if isinstance(btn, _PlusButton):
            btn.set_plus_colors(fg, h["hover_fg"], h["press_fg"], tk("text_dim"))

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
            f"QPushButton:disabled{{"
            f"background:{tk('accent_dis')};border-color:{tk('border')};"
            f"color:{tk('text_dim')};}}"
            f"QPushButton:checked:disabled{{"
            f"background:{on_bg};border-color:{on_bd};color:{on_fg};}}"
        )

    def _refresh_chip_caption(self, btn: QPushButton, on: bool):
        name = str(btn.property("chipName") or "").strip() or (btn.text() or "").strip()
        value = str(btn.property("chipValue") or "").strip()
        btn.setText(name)

    def _chip_matches(self, btn: QPushButton) -> bool:
        key = str(btn.property("promptKey") or "")
        src = str(btn.property("srcKey") or "")
        value = str(btn.property("chipValue") or "").strip()
        if not value:
            return False
        # 按分隔段整段比较 + 长条优先（短条落在长条里面不算），不再用子串
        return value in self._active_values_for(key)

    def _sync_field_chips(self, key: str):
        group = self._chip_groups.get(key)
        if group is None:
            return
        frozen = key in getattr(self, "_frozen_keys", set())
        active = self._active_values_for(key)
        for btn in group.buttons():
            want = str(btn.property("chipValue") or "").strip() in active
            if btn.isChecked() != want:
                btn.blockSignals(True)
                btn.setChecked(want)
                btn.blockSignals(False)
            # 已写入正文时按钮是绿色，仍可点：再点一次把这段从左边删掉
            btn.setEnabled(not frozen)
            btn.setCursor(Qt.ArrowCursor if frozen else Qt.PointingHandCursor)
            self._refresh_chip_caption(btn, want)
        for btn in list(getattr(self, "_add_btns", []) or []):
            try:
                if str(btn.property("presetKey") or "") == key:
                    btn.setEnabled(not frozen)
                    btn.setCursor(Qt.ArrowCursor if frozen else Qt.PointingHandCursor)
            except RuntimeError:
                continue

    def _on_chip_clicked(self, btn: QPushButton):
        key = str(btn.property("promptKey") or "")
        if key in getattr(self, "_frozen_keys", set()):
            self._sync_field_chips(key)
            return
        if self._chip_matches(btn):
            self._remove_chip(btn)
        else:
            self._apply_chip(btn)
        # 自定义 / 内置 / H3 条目只要点过（加入或取消）都提到本栏最前（MRU）
        self._bump_mru(key, str(btn.property("chipValue") or "").strip())

    def _remove_chip(self, btn: QPushButton):
        """绿色（已写入）预设再点一次：从左边正文拿掉这段，按钮恢复常态。"""
        key = str(btn.property("promptKey") or "")
        if key in getattr(self, "_frozen_keys", set()):
            self._sync_field_chips(key)
            return
        value = str(btn.property("chipValue") or "").strip()
        ed = self._left.get(key)
        if ed is None or not value:
            self._sync_field_chips(key)
            return
        new = _strip_chip_segments(ed.toPlainText() or "", value, self._field_chip_values(key))
        if new == (ed.toPlainText() or ""):
            self._sync_field_chips(key)
            return
        ed.setPlainText(new)

    def _apply_chip(self, btn: QPushButton):
        key = str(btn.property("promptKey") or "")
        if key in getattr(self, "_frozen_keys", set()):
            self._sync_field_chips(key)
            return
        value = str(btn.property("chipValue") or "").strip()
        ed = self._left.get(key)
        if ed is None or not value:
            self._sync_field_chips(key)
            return
        if self._chip_matches(btn):
            self._sync_field_chips(key)
            return
        ed.setPlainText(_join_chip_value(ed.toPlainText() or "", value))

    def _add_custom_preset(self, key: str):
        if key in getattr(self, "_frozen_keys", set()):
            return
        dlg = _AddPresetDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            return
        name, value = dlg.pair()
        if not name or not value:
            return
        items = list(self._custom_presets.get(key) or [])
        items.append({"name": name, "value": value})
        self._custom_presets[key] = items
        mru = getattr(self, "_preset_mru", None)
        if mru is not None:
            # 新建的自定义预设直接排到本栏第 1 位，保证马上看得见
            mru[key] = ([value] + [v for v in (mru.get(key) or []) if v != value])[:_MRU_MAX]
        self._rebuild_field_chips(key)
        ed = self._left.get(key)
        if ed is not None and value not in (ed.toPlainText() or ""):
            cur = (ed.toPlainText() or "").rstrip()
            if not cur:
                ed.setPlainText(value)
            else:
                ed.setPlainText(cur + ", " + value)
        self._publish_presets()
        QTimer.singleShot(0, lambda k=key: self._sync_section_row(k))

    def _chip_context_menu(self, btn: QPushButton, pos):
        if not bool(btn.property("chipCustom")):
            return
        menu = QMenu(self)
        act = menu.addAction("删除预设")
        chosen = menu.exec_(btn.mapToGlobal(pos))
        if chosen is not act:
            return
        key = str(btn.property("promptKey") or "")
        name = str(btn.property("chipName") or "").strip()
        value = str(btn.property("chipValue") or "").strip()
        items = [
            it for it in (self._custom_presets.get(key) or [])
            if not (
                str((it or {}).get("name") or "").strip() == name
                and str((it or {}).get("value") or "").strip() == value
            )
        ]
        if items:
            self._custom_presets[key] = items
        else:
            self._custom_presets.pop(key, None)
        self._rebuild_field_chips(key)
        self._publish_presets()
        QTimer.singleShot(0, lambda k=key: self._sync_section_row(k))

    def _publish_presets(self):
        """自定义预设是各分页共用的。改完通知容器刷新每一页并落盘。"""
        host = getattr(self, "_preset_host", None)
        if host is not None and hasattr(host, "sync_shared_presets"):
            host.sync_shared_presets()
            return
        self._schedule_persist()

    def _toggle_freeze_field(self, key: str):
        if key in self._frozen_keys:
            self._frozen_keys.discard(key)
        else:
            self._frozen_keys.add(key)
        self._apply_freeze_visual(key)
        self._refresh_full_prompt()
        self._schedule_persist()

    def _unfreeze_field(self, key: str):
        if key not in getattr(self, "_frozen_keys", set()):
            return
        self._toggle_freeze_field(key)

    def _apply_freeze_visual(self, key: str):
        frozen = key in getattr(self, "_frozen_keys", set())
        lab = self._title_labs.get(key)
        bar = self._title_bar_by_key.get(key)
        if bar is not None and lab is not None:
            self._style_field_bar(bar, lab)
        btn = self._freeze_btns.get(key)
        if btn is not None:
            self._style_freeze_btn(btn)
        ed = self._left.get(key)
        if ed is not None:
            ed.setReadOnly(frozen)
        self._sync_field_chips(key)
        self._place_freeze_veil(key)
        card = self._order_cards.get(key)
        if card is not None:
            self._style_order_card(card, selected=(key == self._selected_key))
        paste = None
        for b in list(getattr(self, "_preset_paste_btns", []) or []):
            try:
                if str(b.property("presetKey") or "") == key:
                    paste = b
                    break
            except RuntimeError:
                continue
        if paste is not None:
            self._style_preset_paste_btn(paste)

    def _place_freeze_veil(self, key: str):
        wrap = self._sections.get(key)
        veil = self._freeze_veils.get(key)
        bar = self._title_bar_by_key.get(key)
        if wrap is None or veil is None:
            return
        frozen = key in getattr(self, "_frozen_keys", set())
        if not frozen:
            veil.hide()
            return
        # 只压编辑框+预设，绝不盖住左侧标题（冻结按钮要能点）
        if bar is not None and bar.width() > 0:
            x1 = bar.x() + bar.width() + 6
        else:
            left = self._left.get(key)
            x1 = left.x() if left is not None else 0
        w = wrap.width() - x1
        h = wrap.height()
        if w <= 0 or h <= 0:
            veil.hide()
            return
        veil.setGeometry(x1, 0, w, h)
        veil.show()
        veil.raise_()

    def _style_freeze_btn(self, btn: QPushButton):
        key = str(btn.property("fieldKey") or "")
        frozen = key in getattr(self, "_frozen_keys", set())
        h = self._hot_colors()
        if frozen:
            if theme.is_dark:
                bd, bg, ico = "#38bdf8", "#0c4a6e", "#7dd3fc"
            else:
                bd, bg, ico = "#0284c7", "#e0f2fe", "#0369a1"
        else:
            if theme.is_dark:
                bd, bg, ico = "#475569", "#1e293b", tk("text_dim")
            else:
                bd, bg, ico = "#94a3b8", "#e2e8f0", tk("text_dim")
        btn.setToolTip("")
        btn.setStyleSheet(
            f"QPushButton{{"
            f"min-width:{_TITLE_ACT}px;max-width:{_TITLE_ACT}px;"
            f"min-height:{_TITLE_ACT}px;max-height:{_TITLE_ACT}px;"
            f"padding:0;border-radius:4px;"
            f"border:1px solid {bd};background:{bg};}}"
            f"QPushButton:hover{{"
            f"background:{h['hover_bg']};border-color:{h['hover_bd']};}}"
            f"QPushButton:pressed{{"
            f"background:{h['press_bg']};border-color:{h['press_bd']};}}"
        )
        btn.setIcon(_freeze_icon(frozen, _PASTE_ICON, ico))

    def _make_full_prompt_section(self) -> QWidget:
        wrap = _PromptSection(self, "full")
        wrap.setObjectName("PromptFullSection")
        wrap.setAttribute(Qt.WA_StyledBackground, True)
        wrap.setStyleSheet("background:transparent;border:none;")
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.setAlignment(Qt.AlignTop)
        bar = QFrame()
        bar.setObjectName("PromptFullBar")
        bar.setFrameShape(QFrame.NoFrame)
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setAutoFillBackground(True)
        bar.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        bar_lay = QVBoxLayout(bar)
        bar_lay.setContentsMargins(_TITLE_BAR_PAD_H, 0, _TITLE_BAR_PAD_H, 0)
        bar_lay.setSpacing(0)
        lab = _VTitle()
        lab.set_title("完整提示词")
        # 没有冻结钮，五个字在标题栏里垂直居中
        bar_lay.addStretch(1)
        bar_lay.addWidget(lab, 0, Qt.AlignHCenter)
        bar_lay.addStretch(1)
        self._full_prompt_bar = bar
        self._full_prompt_lab = lab
        edit = self._make_edit("blue", "full", "组装结果会显示在这里", persist=False)
        edit.setReadOnly(True)
        edit.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        edit._after_fit = lambda: self._sync_full_prompt_layout()
        self._full_prompt = edit
        self._full_wrap = wrap
        row.addWidget(bar, 0)
        row.addWidget(edit, 0)
        self._style_full_title_bar()
        QTimer.singleShot(0, self._refresh_full_prompt)
        return wrap

    def _style_full_title_bar(self):
        bar = getattr(self, "_full_prompt_bar", None)
        lab = getattr(self, "_full_prompt_lab", None)
        if bar is None or lab is None:
            return
        bg, fg = "#202020", "#e4e4e7"
        bar.setStyleSheet(
            f"QFrame#PromptFullBar{{"
            f"background:{bg};border:none;border-radius:4px;}}"
        )
        lab.set_color(fg)
        inner = _title_inner_width()
        bar.setFixedWidth(inner + 2 * _TITLE_BAR_PAD_H)
        lab.setFixedWidth(inner)
        lab.setFixedHeight(max(1, len(getattr(lab, "_chars", []) or [])) * _title_line_height())
        pal = bar.palette()
        pal.setColor(QPalette.Window, QColor(bg))
        pal.setColor(QPalette.Base, QColor(bg))
        bar.setPalette(pal)

    def _sync_full_prompt_layout(self):
        wrap = getattr(self, "_full_wrap", None)
        bar = getattr(self, "_full_prompt_bar", None)
        edit = getattr(self, "_full_prompt", None)
        lab = getattr(self, "_full_prompt_lab", None)
        if wrap is None or bar is None or edit is None:
            return
        if getattr(self, "_syncing_full", False):
            return
        self._syncing_full = True
        try:
            total = wrap.width()
            title_w = bar.width()
            rest = max(1, total - title_w - 6)
            if edit.width() != rest:
                edit.setFixedWidth(rest)
            n = max(1, len(getattr(lab, "_chars", []) or [])) if lab is not None else 1
            title_h = n * _title_line_height() + 6
            h = max(int(edit.needed_height()), int(title_h))
            edit.apply_height(h)
            if bar.minimumHeight() != h or bar.maximumHeight() != h:
                bar.setFixedHeight(h)
        finally:
            self._syncing_full = False

    def _refresh_full_prompt(self):
        edit = getattr(self, "_full_prompt", None)
        if edit is None:
            return
        text = _fullwidth_symbols_to_halfwidth(self._compose("orig"))
        if edit.toPlainText() != text:
            edit.blockSignals(True)
            edit.setPlainText(text)
            edit.blockSignals(False)
            self._style_one_edit(edit, "blue")
        fit = getattr(edit, "_fit_height", None)
        if callable(fit):
            fit()

    def _make_order_box(self) -> QFrame:
        box = QFrame()
        box.setObjectName("PromptOrderBox")
        box.setFrameShape(QFrame.NoFrame)
        box.setAttribute(Qt.WA_StyledBackground, True)
        box.setAutoFillBackground(False)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._make_order_row())
        self._order_box = box
        self._style_order_box()
        return box

    def _make_order_row(self) -> QWidget:
        row_w = QWidget()
        row_w.setObjectName("PromptOrderRow")
        row_w.setAttribute(Qt.WA_StyledBackground, True)
        row_w.setStyleSheet("#PromptOrderRow{background:transparent;border:none;}")
        row = QHBoxLayout(row_w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        cards_host = QWidget()
        cards_host.setObjectName("PromptOrderCards")
        cards_host.setAttribute(Qt.WA_StyledBackground, True)
        cards_host.setStyleSheet("#PromptOrderCards{background:transparent;border:none;}")
        self._order_cards_host = cards_host
        strip = QHBoxLayout(cards_host)
        strip.setContentsMargins(0, 0, 0, 0)
        strip.setSpacing(6)
        strip.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._order_flow = strip
        scroll = _HScrollArea()
        scroll.setObjectName("PromptOrderScroll")
        scroll.setWidgetResizable(False)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        scroll.setAttribute(Qt.WA_StyledBackground, True)
        scroll.setStyleSheet(
            "QScrollArea#PromptOrderScroll{background:transparent;border:none;}"
            "QScrollArea#PromptOrderScroll > QWidget > QWidget{background:transparent;}"
        )
        try:
            scroll.viewport().setAutoFillBackground(False)
            scroll.viewport().setStyleSheet("background:transparent;")
        except Exception:
            pass
        scroll.setWidget(cards_host)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        scroll.setFixedHeight(_ORDER_CARD_H + 16)
        self._order_scroll = scroll
        row.addWidget(scroll, 1)
        return row_w

    def _make_order_action_btn(self, text: str, tip: str) -> QPushButton:
        btn = apply_mini_button(QPushButton(text))
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setFixedHeight(_COPY_H)
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        btn.setMinimumWidth(52)
        btn.setStyleSheet(self._order_btn_qss())
        return btn

    def _make_edit(self, tint: str, key: str, placeholder: str, persist: bool = True) -> _GrowEdit:
        edit = _GrowEdit()
        edit.setObjectName(f"PromptFieldEdit_{tint}_{key}")
        edit.setProperty("promptKey", key)
        # 内容栏不放占位正文：删空后若仍显示灰色例句，会误以为没删干净。
        if key == "full":
            edit.setPlaceholderText(placeholder or "")
        else:
            edit.setPlaceholderText("")
            edit.setToolTip("")
        edit.textChanged.connect(lambda _ed=edit, _t=tint: self._style_one_edit(_ed, _t))
        if persist:
            edit.textChanged.connect(self._schedule_persist)
        self._style_one_edit(edit, tint)
        return edit

    def _make_xfer(self, key: str) -> QWidget:
        wrap = QWidget()
        wrap.setFixedWidth(_XFER_W)
        wrap.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        col = QVBoxLayout(wrap)
        col.setContentsMargins(2, 0, 2, 0)
        col.setSpacing(4)
        col.addStretch(1)
        paste_l = self._make_paste_icon_btn("orig", key)
        paste_r = self._make_paste_icon_btn("new", key)
        btn_l = self._make_xfer_btn("to_left", key)
        btn_r = self._make_xfer_btn("to_right", key)
        # 上蓝（粘贴左 / 移到左），下绿（移到右 / 粘贴右）
        col.addWidget(paste_l)
        col.addWidget(btn_l)
        col.addWidget(btn_r)
        col.addWidget(paste_r)
        col.addStretch(1)
        return wrap

    def _make_xfer_btn(self, direction: str, key: str) -> QPushButton:
        to_left = direction == "to_left"
        btn = QPushButton("←" if to_left else "→")
        btn.setProperty("xferTint", "blue" if to_left else "green")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setFixedSize(_XFER_BTN, _XFER_BTN)
        self._style_xfer_btn(btn)
        btn.clicked.connect(lambda _=False, k=key, d=direction: self._move_field(k, d))
        self._xfer_btns.append(btn)
        return btn

    def _style_xfer_btn(self, btn: QPushButton):
        tint = str(btn.property("xferTint") or "blue")
        btn.setStyleSheet(self._paste_tint_qss(tint, _XFER_BTN))

    def _make_paste_icon_btn(self, side: str, key: str) -> QPushButton:
        btn = QPushButton()
        btn.setProperty("pasteSide", side)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setFixedSize(_XFER_BTN, _XFER_BTN)
        btn.setIconSize(QSize(_PASTE_ICON, _PASTE_ICON))
        self._style_paste_btn(btn)
        btn.clicked.connect(lambda _=False, s=side, k=key: self._paste_field(s, k))
        self._paste_btns.append(btn)
        return btn

    def _paste_tint(self, btn: QPushButton) -> str:
        return "blue" if str(btn.property("pasteSide") or "") == "orig" else "green"

    def _style_paste_btn(self, btn: QPushButton):
        tint = self._paste_tint(btn)
        btn.setStyleSheet(self._paste_tint_qss(tint, _XFER_BTN))
        btn.setIcon(_clipboard_icon(_PASTE_ICON, _tint_colors(tint)["border"]))

    def _make_copy_all_btn(self, side: str) -> QPushButton:
        btn = apply_mini_button(QPushButton("复制全部"))
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        btn.setFixedHeight(_COPY_H)
        btn.setStyleSheet(self._copy_all_qss())
        if side == "orig":
            self.btn_copy_orig = btn
            btn.clicked.connect(lambda: self._copy_side("orig"))
        else:
            self.btn_copy_new = btn
            btn.clicked.connect(lambda: self._copy_side("new"))
        return btn

    def _make_hist_btn(self, kind: str) -> QPushButton:
        btn = QPushButton()
        btn.setProperty("histKind", kind)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setFixedSize(_COPY_H, _COPY_H)
        btn.setIconSize(QSize(_HIST_ICON, _HIST_ICON))
        self._style_hist_btn(btn)
        return btn

    def _make_hist_count(self) -> QLabel:
        lab = QLabel("0")
        lab.setObjectName("PromptHistCount")
        lab.setAlignment(Qt.AlignCenter)
        lab.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lab.setFixedWidth(18)
        lab.setStyleSheet(self._hist_count_qss())
        return lab

    def _style_hist_btn(self, btn: QPushButton):
        kind = str(btn.property("histKind") or "undo")
        btn.setStyleSheet(self._hist_btn_qss())
        color = tk("text") if btn.isEnabled() else tk("text_dim")
        btn.setIcon(_hist_icon(kind, _HIST_ICON, color))

    @staticmethod
    def _hot_colors() -> dict:
        """按钮悬停 / 按下：比 hover_veil 明显一截。"""
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
    def _paste_hot(tint: str) -> dict:
        if tint == "green":
            if theme.is_dark:
                return {
                    "hover_bg": "#166534",
                    "hover_bd": "#86efac",
                    "hover_fg": "#f0fdf4",
                    "press_bg": "#15803d",
                    "press_bd": "#bbf7d0",
                    "press_fg": "#ffffff",
                }
            return {
                "hover_bg": "#16a34a",
                "hover_bd": "#15803d",
                "hover_fg": "#ffffff",
                "press_bg": "#15803d",
                "press_bd": "#166534",
                "press_fg": "#ffffff",
            }
        return _PromptSheet._hot_colors()

    def _paste_tint_qss(self, tint: str, btn_h: int) -> str:
        c = _tint_colors(tint)
        h = self._paste_hot(tint)
        return (
            f"QPushButton{{"
            f"min-width:{_XFER_BTN}px;max-width:{_XFER_BTN}px;"
            f"min-height:{btn_h}px;max-height:{btn_h}px;padding:0;margin:0;"
            f"border:1px solid {c['border']};border-radius:4px;"
            f"background:{c['filled']};color:{c['border']};}}"
            f"QPushButton:hover{{"
            f"background:{h['hover_bg']};border-color:{h['hover_bd']};color:{h['hover_fg']};}}"
            f"QPushButton:pressed{{"
            f"background:{h['press_bg']};border-color:{h['press_bd']};color:{h['press_fg']};}}"
        )

    @staticmethod
    def _hist_btn_qss() -> str:
        h = _PromptSheet._hot_colors()
        return (
            f"QPushButton{{"
            f"min-width:{_COPY_H}px;max-width:{_COPY_H}px;"
            f"min-height:{_COPY_H}px;max-height:{_COPY_H}px;"
            f"padding:0;margin:0;border-radius:6px;"
            f"border:1px solid {tk('border')};"
            f"background:{tk('input_bg')};}}"
            f"QPushButton:hover{{"
            f"background:{h['hover_bg']};border-color:{h['hover_bd']};}}"
            f"QPushButton:pressed{{"
            f"background:{h['press_bg']};border-color:{h['press_bd']};}}"
            f"QPushButton:disabled{{"
            f"background:{tk('accent_dis')};border-color:{tk('border')};}}"
        )

    @staticmethod
    def _hist_count_qss() -> str:
        return (
            f"QLabel#PromptHistCount{{"
            f"background:transparent;border:none;padding:0;"
            f"color:{tk('text_mut')};font-size:14px;font-weight:700;}}"
        )

    @staticmethod
    def _xfer_qss(btn_h: int = _XFER_BTN) -> str:
        h = _PromptSheet._hot_colors()
        return (
            f"QPushButton{{"
            f"min-width:{_XFER_BTN}px;max-width:{_XFER_BTN}px;"
            f"min-height:{btn_h}px;max-height:{btn_h}px;padding:0;margin:0;"
            f"border:1px solid {tk('border')};border-radius:4px;"
            f"background:{tk('input_bg')};color:{tk('text')};font-size:12px;}}"
            f"QPushButton:hover{{"
            f"background:{h['hover_bg']};border-color:{h['hover_bd']};color:{h['hover_fg']};}}"
            f"QPushButton:pressed{{"
            f"background:{h['press_bg']};border-color:{h['press_bd']};color:{h['press_fg']};}}"
        )

    @staticmethod
    def _copy_all_qss() -> str:
        h = _PromptSheet._hot_colors()
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
            f"QPushButton:disabled{{"
            f"background:{tk('accent_dis')};border-color:{tk('border')};"
            f"color:{tk('text_dim')};}}"
        )

    def _style_field_square_btn(self, btn: QPushButton):
        qss = self._order_btn_qss().replace("padding:0 12px;", "padding:0;")
        qss += (
            "QPushButton{"
            f"min-width:{_COPY_H}px;max-width:{_COPY_H}px;"
            f"min-height:{_COPY_H}px;max-height:{_COPY_H}px;"
            "font-size:18px;font-weight:700;}"
        )
        btn.setStyleSheet(qss)

    @staticmethod
    def _order_btn_qss() -> str:
        h = _PromptSheet._hot_colors()
        return (
            f"QPushButton{{"
            f"min-height:{_COPY_H}px;max-height:{_COPY_H}px;padding:0 12px;border-radius:6px;"
            f"border:1px solid {tk('border')};"
            f"background:{tk('input_bg')};color:{tk('text')};"
            f"font-size:13px;font-weight:700;}}"
            f"QPushButton:hover{{"
            f"background:{h['hover_bg']};border-color:{h['hover_bd']};color:{h['hover_fg']};}}"
            f"QPushButton:pressed{{"
            f"background:{h['press_bg']};border-color:{h['press_bd']};color:{h['press_fg']};}}"
            f"QPushButton:disabled{{"
            f"background:{tk('accent_dis')};border-color:{tk('border')};"
            f"color:{tk('text_dim')};}}"
        )

    def _style_order_card(self, card: _OrderCard, selected: bool = False, refresh_title: bool = True):
        frozen = card._key in getattr(self, "_frozen_keys", set())
        accent = _field_accent(card._key)
        border_w = _ORDER_CARD_BD + (2 if selected else 0)
        if frozen:
            bg = "#111111" if selected else "#080808"
            bd = "#ffffff" if selected else "#3f3f46"
            fg = "#f8fafc"
        else:
            bg = accent
            if selected:
                bg = _mix_hex(bg, "#fde68a", 0.18)
                bd = "#ffffff"
            else:
                bd = _mix_hex(accent, "#1e293b", 0.22)
            fg = _contrast_fg(bg)
        card.setAutoFillBackground(False)
        card.setAttribute(Qt.WA_StyledBackground, True)
        card.setFixedSize(_order_card_w(), _ORDER_CARD_H)
        card.setStyleSheet(
            f"QFrame#PromptOrderCard{{"
            f"background-color:{bg};border:{border_w}px solid {bd};border-radius:6px;}}"
            f"QFrame#PromptOrderCard QLabel{{"
            f"background:transparent;border:none;color:{fg};"
            f"font-size:11px;font-weight:600;}}"
            f"QFrame#PromptOrderCard QPlainTextEdit#PromptOrderCardEdit{{"
            f"background:transparent;color:{fg};border:none;"
            f"padding:0;font-size:11px;font-weight:600;}}"
        )
        btn = getattr(card, "btn_freeze", None)
        if btn is not None:
            editing = bool(getattr(card, "_editing", False))
            btn.setVisible(not editing)
            if frozen:
                # 解冻钮：保持原来的暗底浅雪花
                hov = self._hot_colors()
                bbd, bbg, ico = "#e4e4e7", "#1a1a1a", "#f8fafc"
                hover_bg, hover_bd = hov["hover_bg"], hov["hover_bd"]
                press_bg, press_bd = hov["press_bg"], hov["press_bd"]
            else:
                # 冻结钮：框里填内容卡同色，雪花用标题对比色
                bbd, bbg, ico = bd, bg, fg
                hover_bg = _mix_hex(bg, fg, 0.16)
                hover_bd = fg
                press_bg = _mix_hex(bg, fg, 0.28)
                press_bd = fg
            btn.setStyleSheet(
                f"QPushButton#PromptOrderFreeze{{"
                f"background:{bbg};border:1px solid {bbd};border-radius:4px;padding:0;"
                f"min-width:{_TITLE_ACT}px;max-width:{_TITLE_ACT}px;"
                f"min-height:{_TITLE_ACT}px;max-height:{_TITLE_ACT}px;}}"
                f"QPushButton#PromptOrderFreeze:hover{{"
                f"background:{hover_bg};border-color:{hover_bd};}}"
                f"QPushButton#PromptOrderFreeze:pressed{{"
                f"background:{press_bg};border-color:{press_bd};}}"
            )
            btn.setIcon(_freeze_icon(frozen, _PASTE_ICON, ico))
            btn.setToolTip("")
            card._place_freeze()
        card.setFixedSize(_order_card_w(), _ORDER_CARD_H)
        if refresh_title:
            apply = getattr(card, "apply_title", None)
            if callable(apply):
                apply()

    def _style_order_box(self):
        box = getattr(self, "_order_box", None)
        if box is None:
            return
        box.setStyleSheet(
            "QFrame#PromptOrderBox{background:transparent;border:none;}"
        )

    @staticmethod
    def _field_bar_bg() -> str:
        # panel 跟窗底太近，看不出通栏；row_bg 深浅主题都压得住
        return tk("row_bg")

    def _style_field_bar(self, bar: QFrame, lab: _VTitle):
        key = str(bar.property("fieldKey") or "")
        frozen = key in getattr(self, "_frozen_keys", set())
        accent = _field_accent(key)
        bg = _mix_hex(accent, "#1e293b", 0.22) if frozen else accent
        fg = _contrast_fg(bg)
        # 必须用 #id：全站末尾有 QLabel{background:transparent}，同特异性会盖掉
        bar.setStyleSheet(
            f"QFrame#PromptFieldBar{{"
            f"background:{bg};border:none;border-radius:4px;}}"
        )
        lab.set_color(fg)
        inner = _title_inner_width()
        bar.setFixedWidth(inner + 2 * _TITLE_BAR_PAD_H)
        lab.setFixedWidth(inner)
        lab.setFixedHeight(max(1, len(getattr(lab, "_chars", []) or [])) * _title_line_height())
        lab.updateGeometry()
        lab.update()
        pal = bar.palette()
        pal.setColor(QPalette.Window, QColor(bg))
        pal.setColor(QPalette.Base, QColor(bg))
        bar.setPalette(pal)

    def _style_one_edit(self, edit: QPlainTextEdit, tint: str):
        key = str(edit.property("promptKey") or "")
        empty = not (edit.toPlainText() or "").strip()
        if key == "full":
            bg = "#000000" if empty else "#080808"
            fg = "#8a8a8a" if empty else "#e4e4e7"
            ph = QColor("#6b6b6b")
        elif key:
            accent = _field_accent(key)
            toward = _fade_toward()
            bg = _mix_hex(accent, toward, 0.90) if empty else _fade80(accent)
            fg = tk("text")
            ph = QColor(tk("text_dim"))
        else:
            c = _tint_colors(tint)
            bg = c["empty"] if empty else c["filled"]
            fg = c["placeholder"] if empty else tk("text")
            ph = QColor(c["placeholder"])
        name = edit.objectName()
        edit.setStyleSheet(
            f"QPlainTextEdit#{name}{{"
            f"background:{bg};color:{fg};"
            f"border:none;border-radius:4px;"
            f"padding:{_EDIT_PAD_V}px {_EDIT_PAD_H}px;font-size:13px;}}"
            f"QPlainTextEdit#{name} QScrollBar:vertical,"
            f"QPlainTextEdit#{name} QScrollBar:horizontal{{"
            f"width:0px;height:0px;margin:0;}}"
        )
        pal = edit.palette()
        try:
            pal.setColor(QPalette.PlaceholderText, ph)
        except Exception:
            pass
        pal.setColor(QPalette.Text, QColor(fg))
        pal.setColor(QPalette.Base, QColor(bg))
        edit.setPalette(pal)

    def restyle_theme(self, *_):
        self._prune_dead()
        for bar, lab in zip(self._title_bars, self._labels):
            self._style_field_bar(bar, lab)
        for btn in list(getattr(self, "_freeze_btns", {}).values()):
            try:
                btn.objectName()
            except RuntimeError:
                continue
            self._style_freeze_btn(btn)
        self._style_order_box()
        for key, _t, _p in self._specs:
            if key in self._left:
                self._style_one_edit(self._left[key], "blue")
            if key in self._right:
                self._style_one_edit(self._right[key], "green")
        for b in self._xfer_btns:
            self._style_xfer_btn(b)
        for b in self._paste_btns:
            self._style_paste_btn(b)
        copy_qss = self._copy_all_qss()
        btn = getattr(self, "btn_copy_orig", None)
        if btn is not None:
            btn.setStyleSheet(copy_qss)
        preview = getattr(self, "_full_prompt", None)
        if preview is not None:
            self._style_one_edit(preview, "blue")
        self._style_full_title_bar()
        self._sync_full_prompt_layout()
        for attr in ("btn_undo", "btn_redo"):
            btn = getattr(self, attr, None)
            if btn is not None:
                self._style_hist_btn(btn)
        count = getattr(self, "lbl_hist_count", None)
        if count is not None:
            count.setStyleSheet(self._hist_count_qss())
        for attr in ("btn_field_add", "btn_field_del"):
            btn = getattr(self, attr, None)
            if btn is not None:
                self._style_field_square_btn(btn)
        for key, card in self._order_cards.items():
            self._style_order_card(card, selected=(key == self._selected_key))
        for pane in self._preset_panes.values():
            self._style_preset_pane(pane)
        for btn in list(self._chip_btns):
            try:
                btn.objectName()
            except RuntimeError:
                continue
            self._style_chip(btn)
        for btn in list(getattr(self, "_h3_toggles", {}).values()):
            try:
                btn.objectName()
            except RuntimeError:
                continue
            self._style_h3_toggle(btn)
        for key in list(self._chip_groups.keys()):
            self._sync_field_chips(key)
        for btn in list(self._add_btns):
            try:
                btn.objectName()
            except RuntimeError:
                continue
            self._style_add_preset_btn(btn)
        self._refresh_preset_paste_btns()
        for key in self._spec_keys():
            self._place_freeze_veil(key)
        ghost = getattr(self, "_drag_ghost", None)
        if ghost is not None:
            self._style_order_ghost(ghost)
        self._refresh_hist_btns()
        self._fit_all_edits()

    def on_enter(self):
        self._refresh_preset_paste_btns()
        self._sync_h3_all()
        QTimer.singleShot(0, self._fit_all_edits)
        QTimer.singleShot(0, self._sync_foot_align)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._sync_foot_align()

    def _sync_foot_align(self, *_):
        """底栏 / 内容卡让出滚动条占位，与上方内容左右齐平。"""
        scroll = getattr(self, "_scroll", None)
        wrap = getattr(self, "_foot_wrap", None)
        if scroll is None or wrap is None:
            return
        gutter = max(0, scroll.width() - scroll.viewport().width())
        m = wrap.contentsMargins()
        if m.right() != gutter or m.left() != 0:
            wrap.setContentsMargins(0, 0, gutter, 0)

    def _alive(self, widgets):
        out = []
        for w in widgets:
            try:
                w.objectName()
            except RuntimeError:
                continue
            out.append(w)
        return out

    def _prune_dead(self):
        self._title_bars = self._alive(self._title_bars)
        self._labels = self._alive(self._labels)
        self._xfer_btns = self._alive(self._xfer_btns)
        self._paste_btns = self._alive(self._paste_btns)

    def _spec_keys(self) -> List[str]:
        return [k for k, _t, _p in self._specs]

    def _export_custom(self) -> List[dict]:
        builtin = {k for k, _t, _p in _FIELDS}
        return [
            {"key": k, "title": t, "placeholder": p}
            for k, t, p in self._specs
            if k not in builtin
        ]

    def _export_custom_presets(self) -> dict:
        out = {}
        for key, items in (self._custom_presets or {}).items():
            cleaned = []
            for it in items or []:
                name = str((it or {}).get("name") or "").strip()
                value = str((it or {}).get("value") or "").strip()
                if name and value:
                    cleaned.append({"name": name, "value": value})
            if cleaned:
                out[str(key)] = cleaned
        return out

    @staticmethod
    def _parse_custom_presets(data: dict) -> Dict[str, List[dict]]:
        raw = data.get("custom_presets") if isinstance(data, dict) else None
        out: Dict[str, List[dict]] = {}
        if not isinstance(raw, dict):
            return out
        for key, items in raw.items():
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
                out[str(key)] = cleaned
        return out

    def _specs_from_saved(self, data: dict) -> List[Tuple[str, str, str]]:
        custom = {}
        raw_custom = data.get("custom_fields") if isinstance(data, dict) else None
        if isinstance(raw_custom, list):
            for item in raw_custom:
                if not isinstance(item, dict):
                    continue
                k = str(item.get("key") or "").strip()
                if not k:
                    continue
                custom[k] = (
                    k,
                    str(item.get("title") or "自定义").strip() or "自定义",
                    str(item.get("placeholder") or _CUSTOM_PLACEHOLDER),
                )
        cat = _catalog_map()
        order = data.get("field_order") if isinstance(data, dict) else None
        if not isinstance(order, list) or not order:
            specs = list(_FIELDS)
        else:
            specs = []
            seen = set()
            for raw in order:
                k = str(raw)
                if k in seen:
                    continue
                if k in cat:
                    specs.append(cat[k])
                    seen.add(k)
                elif k in custom:
                    specs.append(custom[k])
                    seen.add(k)
            if not specs:
                specs = list(_FIELDS)
        specs = _with_missing_builtins(specs)
        titles = data.get("field_titles") if isinstance(data, dict) else None
        if isinstance(titles, dict) and titles:
            patched = []
            for k, t, p in specs:
                nt = titles.get(k)
                if nt is None:
                    patched.append((k, t, p))
                else:
                    patched.append((k, str(nt).strip() or t, p))
            specs = patched
        return specs

    def _fit_order_strip(self):
        host = getattr(self, "_order_cards_host", None)
        lay = getattr(self, "_order_flow", None)
        if host is None or lay is None:
            return
        spacing = lay.spacing()
        width = 0
        count = 0
        for i in range(lay.count()):
            item = lay.itemAt(i)
            w = item.widget() if item is not None else None
            if w is None:
                continue
            width += max(w.width(), w.sizeHint().width(), 1)
            count += 1
        if count > 1:
            width += spacing * (count - 1)
        host.setFixedSize(max(1, width), _ORDER_CARD_H)

    def _ensure_order_card_visible(self, key: str):
        scroll = getattr(self, "_order_scroll", None)
        card = self._order_cards.get(key)
        if scroll is None or card is None:
            return
        try:
            scroll.ensureWidgetVisible(card, 12, 0)
        except Exception:
            pass

    def _rebuild_order_cards(self):
        flow = getattr(self, "_order_flow", None)
        if flow is None:
            return
        self._commit_order_rename()
        self._cancel_order_drag_visual()
        while flow.count():
            item = flow.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._order_cards = {}
        for key, title, _p in self._specs:
            card = _OrderCard(key, title, self)
            self._style_order_card(card, selected=(key == self._selected_key))
            flow.addWidget(card)
            self._order_cards[key] = card
        self._fit_order_strip()
        self._refresh_order_btns()

    def _refresh_order_btns(self):
        delete = getattr(self, "btn_field_del", None)
        if delete is not None:
            delete.setEnabled(bool(self._selected_key) and len(self._specs) > 1)

    def _select_order_card(self, key: str):
        prev = self._selected_key
        if prev == key:
            return
        self._selected_key = key
        # 只改前后两张卡的边色，不重排、不改标题，旁边的卡不会抖
        if prev and prev in self._order_cards:
            self._style_order_card(self._order_cards[prev], selected=False, refresh_title=False)
        card = self._order_cards.get(key)
        if card is not None:
            self._style_order_card(card, selected=True, refresh_title=False)
        self._refresh_order_btns()

    def _clear_order_selection(self):
        if not self._selected_key:
            return
        self._commit_order_rename()
        prev = self._selected_key
        self._selected_key = None
        card = self._order_cards.get(prev)
        if card is not None:
            self._style_order_card(card, selected=False, refresh_title=False)
        self._refresh_order_btns()

    def _click_keeps_order_sel(self, w) -> bool:
        cur = w
        btns = (
            getattr(self, "btn_field_add", None),
            getattr(self, "btn_field_del", None),
        )
        while cur is not None:
            if isinstance(cur, _OrderCard):
                return True
            if cur in btns:
                return True
            cur = cur.parentWidget()
        return False

    def _teardown_click_filter(self, *_):
        if getattr(self, "_click_filter_on", False):
            app = QApplication.instance()
            if app is not None:
                try:
                    app.removeEventFilter(self)
                except Exception:
                    pass
            self._click_filter_on = False
        if getattr(self, "_clip_on", False):
            clip = QApplication.clipboard()
            if clip is not None:
                try:
                    clip.dataChanged.disconnect(self._refresh_preset_paste_btns)
                except Exception:
                    pass
            self._clip_on = False
        timer = getattr(self, "_clip_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
        save_timer = getattr(self, "_save_timer", None)
        if save_timer is not None:
            try:
                save_timer.stop()
            except Exception:
                pass

    def eventFilter(self, obj, ev):
        try:
            if (
                ev.type() == QEvent.MouseButtonPress
                and ev.button() == Qt.LeftButton
                and self._selected_key
                and self.isVisible()
                and self._drag_key is None
            ):
                w = None
                try:
                    w = QApplication.widgetAt(ev.globalPos())
                except Exception:
                    w = obj if isinstance(obj, QWidget) else None
                if w is not None and not self._click_keeps_order_sel(w):
                    self._clear_order_selection()
        except Exception:
            pass
        return False

    def _order_drop_index(self, moving: str, gpos) -> int:
        """插入点：落在某张卡中线左侧则插到它前面。原卡仍在槽里。"""
        keys = self._spec_keys()
        if not keys:
            return 0
        for i, k in enumerate(keys):
            w = self._order_cards.get(k)
            if w is None:
                continue
            r = QRect(w.mapToGlobal(QPoint(0, 0)), w.size())
            if gpos.x() < r.center().x():
                return i
        return len(keys)

    def _move_spec(self, key: str, dest: int) -> bool:
        keys = self._spec_keys()
        if key not in keys:
            return False
        src = keys.index(key)
        keys.pop(src)
        if dest > src:
            dest -= 1
        dest = max(0, min(dest, len(keys)))
        keys.insert(dest, key)
        if keys == self._spec_keys():
            return False
        by = {k: spec for k, spec in ((s[0], s) for s in self._specs)}
        self._specs = [by[k] for k in keys]
        return True

    def _snapshot_order_geoms(self) -> dict:
        out = {}
        for key, card in self._order_cards.items():
            out[key] = QRect(card.geometry())
        return out

    def _run_card_slide(self, pairs):
        if self._card_anim is not None:
            try:
                self._card_anim.stop()
            except Exception:
                pass
            self._card_anim = None
        if not pairs:
            return
        group = QParallelAnimationGroup(self)
        for w, start, end in pairs:
            if start == end:
                continue
            w.setGeometry(start)
            anim = QPropertyAnimation(w, b"geometry", w)
            anim.setDuration(_ORDER_SLIDE_MS)
            anim.setStartValue(start)
            anim.setEndValue(end)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            group.addAnimation(anim)
        if group.animationCount() <= 0:
            return
        self._card_anim = group
        group.finished.connect(lambda: setattr(self, "_card_anim", None))
        group.start(QAbstractAnimation.DeleteWhenStopped)

    def _relayout_order_cards(self, animate: bool = False, old=None):
        flow = getattr(self, "_order_flow", None)
        if flow is None:
            return
        if animate and old is None:
            old = self._snapshot_order_geoms()
        elif not animate:
            old = {}
        while flow.count():
            flow.takeAt(0)
        for key, _t, _p in self._specs:
            card = self._order_cards.get(key)
            if card is not None:
                flow.addWidget(card)
                self._style_order_card(card, selected=(key == self._selected_key))
        try:
            flow.invalidate()
            flow.activate()
        except Exception:
            pass
        self._fit_order_strip()
        if self._drag_ghost is not None and self._drag_insert is not None:
            self._place_drop_guide(self._drag_insert)
        if not animate or not old:
            return
        pairs = []
        for key, card in self._order_cards.items():
            prev = old.get(key)
            now = card.geometry()
            if prev is not None and prev != now:
                pairs.append((card, prev, QRect(now)))
        self._run_card_slide(pairs)

    def _style_order_ghost(self, ghost):
        if ghost is not None:
            ghost.update()

    def _place_drop_guide(self, insert: int):
        ghost = self._drag_ghost
        host = getattr(self, "_order_cards_host", None)
        lay = getattr(self, "_order_flow", None)
        keys = self._spec_keys()
        if ghost is None or host is None or not keys:
            return
        if ghost.parent() is not host:
            ghost.setParent(host)
        spacing = lay.spacing() if lay is not None else 6
        half = _ORDER_GUIDE_W // 2
        y = 0
        if insert <= 0:
            card = self._order_cards.get(keys[0])
            line_x = (card.x() - spacing / 2.0) if card is not None else 0
            y = card.y() if card is not None else 0
        elif insert >= len(keys):
            card = self._order_cards.get(keys[-1])
            line_x = (
                (card.x() + card.width() + spacing / 2.0) if card is not None else 0
            )
            y = card.y() if card is not None else 0
        else:
            left = self._order_cards.get(keys[insert - 1])
            right = self._order_cards.get(keys[insert])
            if left is not None and right is not None:
                line_x = (left.x() + left.width() + right.x()) / 2.0
                y = right.y()
            elif right is not None:
                line_x = right.x() - spacing / 2.0
                y = right.y()
            else:
                line_x = 0
        ghost.move(int(round(line_x - half)), y)
        ghost.show()
        ghost.raise_()

    def _clear_drag_widgets(self):
        preview = getattr(self, "_drag_preview", None)
        self._drag_preview = None
        if preview is not None:
            preview.hide()
            preview.setParent(None)
            preview.deleteLater()
        ghost = self._drag_ghost
        self._drag_ghost = None
        if ghost is not None:
            flow = getattr(self, "_order_flow", None)
            if flow is not None:
                try:
                    flow.removeWidget(ghost)
                except Exception:
                    pass
            ghost.hide()
            ghost.setParent(None)
            ghost.deleteLater()

    def _begin_order_drag(self, key: str, gpos):
        if self._drag_key == key:
            return
        self._commit_order_rename()
        self._cancel_order_drag_visual()
        card = self._order_cards.get(key)
        if card is None:
            return
        self._drag_key = key
        self._drag_grab = card.mapFromGlobal(gpos)
        self._drag_insert = self._spec_keys().index(key) if key in self._spec_keys() else 0
        self._order_dragging = False
        ghost = _DropGuide()
        ghost.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._drag_ghost = ghost
        self._place_drop_guide(self._drag_insert)
        pm = card.grab()
        preview = _DragPreview(pm, self)
        self._drag_preview = preview
        preview.move(card.mapTo(self, QPoint(0, 0)))
        preview.show()
        preview.raise_()
        try:
            card.grabMouse()
        except Exception:
            pass

    def _drag_order_card(self, key: str, gpos):
        if self._drag_key != key:
            self._begin_order_drag(key, gpos)
        card = self._order_cards.get(key)
        preview = getattr(self, "_drag_preview", None)
        if card is None:
            return
        grab = self._drag_grab or QPoint(card.width() // 2, card.height() // 2)
        if preview is not None:
            preview.move(self.mapFromGlobal(gpos) - grab)
            preview.show()
            preview.raise_()
        dest = self._order_drop_index(key, gpos)
        if dest != self._drag_insert:
            self._drag_insert = dest
            self._place_drop_guide(dest)
        src = self._spec_keys().index(key) if key in self._spec_keys() else -1
        self._order_dragging = dest not in (src, src + 1)

    def _cancel_order_drag_visual(self):
        if self._card_anim is not None:
            try:
                self._card_anim.stop()
            except Exception:
                pass
            self._card_anim = None
        card = self._order_cards.get(self._drag_key) if self._drag_key else None
        if card is not None:
            try:
                card.releaseMouse()
            except Exception:
                pass
            card.setCursor(Qt.OpenHandCursor)
        self._clear_drag_widgets()
        self._drag_key = None
        self._drag_grab = None
        self._drag_insert = None

    def _finish_order_drag(self, key: str):
        card = self._order_cards.get(key)
        if card is not None:
            try:
                card.releaseMouse()
            except Exception:
                pass
            card.setCursor(Qt.OpenHandCursor)
        self._clear_drag_widgets()
        self._drag_key = None
        self._drag_grab = None
        self._drag_insert = None
        self._order_dragging = False

    def _end_order_drag(self, key: str):
        if self._drag_key is None:
            return
        dest = self._drag_insert
        old = self._snapshot_order_geoms()
        changed = False
        if dest is not None:
            changed = self._move_spec(key, dest)
        self._finish_order_drag(key)
        if changed:
            self._relayout_order_cards(animate=True, old=old)
            self._apply_section_order()
            self._schedule_persist()

    def _apply_section_order(self):
        for i in range(self._sec_lay.count() - 1, -1, -1):
            self._sec_lay.takeAt(i)
        for key, _t, _p in self._specs:
            wrap = self._sections.get(key)
            if wrap is not None:
                self._sec_lay.addWidget(wrap)
        QTimer.singleShot(0, self._fit_all_edits)

    def _next_custom_key(self) -> str:
        used = set(self._spec_keys())
        n = 1
        while f"custom_{n}" in used:
            n += 1
        return f"custom_{n}"

    def _add_field_clicked(self):
        used = set(self._spec_keys())
        unused = [spec for spec in _FIELDS if spec[0] not in used]
        btn = getattr(self, "btn_field_add", None)
        menu = QMenu(self)
        for key, title, ph in unused:
            act = menu.addAction(title)
            act.setData(("builtin", key, title, ph))
        if unused:
            menu.addSeparator()
        custom_act = menu.addAction("自定义分区…")
        custom_act.setData(("custom",))
        pos = btn.mapToGlobal(btn.rect().bottomLeft()) if btn is not None else self.cursor().pos()
        chosen = menu.exec_(pos)
        if chosen is None:
            return
        data = chosen.data()
        if not data:
            return
        if data[0] == "custom":
            name, ok = QInputDialog.getText(self, "自定义分区", "分区名称：")
            if not ok:
                return
            title = (name or "").strip() or "自定义"
            spec = (self._next_custom_key(), title, _CUSTOM_PLACEHOLDER)
        else:
            spec = (data[1], data[2], data[3])
        self._append_spec(*spec)

    def _append_spec(self, key: str, title: str, placeholder: str):
        if key in self._sections:
            return
        self._specs.append((key, title, placeholder))
        self._add_section_widget(key, title, placeholder)
        self._selected_key = key
        self._rebuild_order_cards()
        self._ensure_order_card_visible(key)
        self._schedule_persist()
        QTimer.singleShot(0, self._fit_all_edits)

    def _rename_selected_field(self):
        key = self._selected_key
        if not key:
            return
        self._commit_order_rename()
        card = self._order_cards.get(key)
        if card is None:
            return
        self._ensure_order_card_visible(key)
        card.begin_rename()

    def _apply_card_rename(self, key: str, title: str):
        title = (title or "").strip()
        if not title:
            return
        cur = ""
        for k, t, _p in self._specs:
            if k == key:
                cur = t
                break
        if title == cur:
            return
        self._specs = [
            (k, title, p) if k == key else (k, t, p)
            for k, t, p in self._specs
        ]
        lab = self._title_labs.get(key)
        if lab is not None:
            self._set_vtitle(lab, title)
            bar = self._title_bar_by_key.get(key)
            if bar is not None:
                self._style_field_bar(bar, lab)
            self._sync_section_row(key)
        card = self._order_cards.get(key)
        if card is not None:
            card.set_title(title)
            self._style_order_card(card, selected=(key == self._selected_key))
        self._schedule_persist()

    def _commit_order_rename(self):
        for card in list(self._order_cards.values()):
            if not card.is_editing():
                continue
            title = card.finish_rename(True)
            if title:
                self._apply_card_rename(card._key, title)
            else:
                card.apply_title()
            self._style_order_card(card, selected=(card._key == self._selected_key))
            return True
        return False

    def _cancel_order_rename(self):
        for card in list(self._order_cards.values()):
            if not card.is_editing():
                continue
            card.finish_rename(False)
            card.apply_title()
            self._style_order_card(card, selected=(card._key == self._selected_key))
            return

    def _delete_selected_field(self):
        self._commit_order_rename()
        key = self._selected_key
        if not key or len(self._specs) <= 1:
            return
        self._specs = [s for s in self._specs if s[0] != key]
        wrap = self._sections.pop(key, None)
        if wrap is not None:
            self._sec_lay.removeWidget(wrap)
            wrap.setParent(None)
            wrap.deleteLater()
        self._left.pop(key, None)
        self._right.pop(key, None)
        self._order_cards.pop(key, None)
        self._title_labs.pop(key, None)
        self._title_bar_by_key.pop(key, None)
        self._right_slots.pop(key, None)
        self._preset_panes.pop(key, None)
        self._preset_flows.pop(key, None)
        self._h3_flows.pop(key, None)
        self._entry_btns.pop(key, None)
        self._field_fixed.pop(key, None)
        self._entry_sig.pop(key, None)
        self._h3_toggles.pop(key, None)
        self._chip_groups.pop(key, None)
        self._custom_presets.pop(key, None)
        self._freeze_btns.pop(key, None)
        self._freeze_veils.pop(key, None)
        self._frozen_keys.discard(key)
        self._selected_key = self._spec_keys()[-1] if self._specs else None
        self._prune_dead()
        self._rebuild_order_cards()
        self._schedule_persist()
        QTimer.singleShot(0, self._fit_all_edits)

    def _adopt_specs(self, specs: List[Tuple[str, str, str]]):
        specs = list(specs) if specs else list(_FIELDS)
        old_keys = self._spec_keys()
        new_keys = [s[0] for s in specs]
        if old_keys == new_keys:
            self._apply_spec_titles(specs)
            self._specs = specs
            return
        if set(old_keys) == set(new_keys) and len(old_keys) == len(new_keys):
            self._specs = specs
            self._apply_section_order()
            self._rebuild_order_cards()
            return
        self._rebuild_all_sections(specs)

    def _apply_spec_titles(self, specs: List[Tuple[str, str, str]]):
        for k, t, _p in specs:
            lab = self._title_labs.get(k)
            if lab is not None:
                self._set_vtitle(lab, t)
                bar = self._title_bar_by_key.get(k)
                if bar is not None:
                    self._style_field_bar(bar, lab)
                self._sync_section_row(k)
            card = self._order_cards.get(k)
            if card is not None:
                card.set_title(t)
                self._style_order_card(card, selected=(k == self._selected_key))

    def _rebuild_all_sections(self, specs: List[Tuple[str, str, str]]):
        for i in range(self._sec_lay.count() - 1, -1, -1):
            item = self._sec_lay.takeAt(i)
            w = item.widget() if item is not None else None
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._sections = {}
        self._left = {}
        self._right = {}
        self._title_bars = []
        self._labels = []
        self._title_labs = {}
        self._title_bar_by_key = {}
        self._right_slots = {}
        self._preset_panes = {}
        self._preset_flows = {}
        self._chip_groups = {}
        self._chip_btns = []
        self._add_btns = []
        self._preset_paste_btns = []
        self._entry_btns = {}
        self._field_fixed = {}
        self._entry_sig = {}
        self._h3_toggles = {}
        self._h3_flows = {}
        self._freeze_btns = {}
        self._freeze_veils = {}
        self._xfer_btns = []
        self._paste_btns = []
        self._specs = list(specs) if specs else list(_FIELDS)
        for key, title, ph in self._specs:
            self._add_section_widget(key, title, ph)
        if self._selected_key not in set(self._spec_keys()):
            self._selected_key = None
        self._rebuild_order_cards()

    def _move_field(self, key: str, direction: str):
        src = self._left[key] if direction == "to_right" else self._right[key]
        dst = self._right[key] if direction == "to_right" else self._left[key]
        text = src.toPlainText()
        if not (text or "").strip():
            return
        dst.setPlainText(text)
        src.setPlainText("")

    def _paste_field(self, side: str, key: str):
        if key in getattr(self, "_frozen_keys", set()):
            return
        try:
            text = QApplication.clipboard().text() or ""
        except Exception:
            text = ""
        if not text:
            return
        edits = self._left if side == "orig" else self._right
        edit = edits.get(key)
        if edit is None:
            return
        edit.setPlainText(_normalize_prompt_paste(text))

    def _compose(self, side: str) -> str:
        edits = self._left if side == "orig" else self._right
        parts = []
        frozen = getattr(self, "_frozen_keys", set())
        for key, _label, _ph in self._specs:
            if key in frozen:
                continue
            ed = edits.get(key)
            if ed is None:
                continue
            text = (ed.toPlainText() or "").strip()
            if not text:
                continue
            text = _fullwidth_symbols_to_halfwidth(text).rstrip()
            if text and not text.endswith("."):
                text += "."
            if text:
                parts.append(text)
        return "\n".join(parts)

    def _copy_side(self, side: str):
        text = self._compose(side)
        if not text:
            return
        QApplication.clipboard().setText(_fullwidth_symbols_to_halfwidth(text))

    def _snapshot(self) -> dict:
        return {
            "orig": self._side_values("orig"),
            "new": self._side_values("new"),
            "field_order": self._spec_keys(),
            "custom_fields": self._export_custom(),
            "field_titles": {k: t for k, t, _p in self._specs},
            "frozen_fields": sorted(self._frozen_keys),
        }

    def _reset_hist(self):
        self._hist = []
        self._redo_stack = []
        self._hist_head = self._snapshot()
        self._refresh_hist_btns()

    def _refresh_hist_btns(self):
        undo = getattr(self, "btn_undo", None)
        redo = getattr(self, "btn_redo", None)
        count = getattr(self, "lbl_hist_count", None)
        if undo is not None:
            undo.setEnabled(bool(self._hist))
            self._style_hist_btn(undo)
        if redo is not None:
            redo.setEnabled(bool(self._redo_stack))
            self._style_hist_btn(redo)
        if count is not None:
            count.setText(str(len(self._hist)))

    def _commit_hist(self):
        if self._hist_applying or self._loading:
            return
        snap = self._snapshot()
        if self._hist_head is None:
            self._hist_head = snap
            self._refresh_hist_btns()
            return
        if snap == self._hist_head:
            return
        self._hist.append(self._hist_head)
        if len(self._hist) > _HIST_MAX:
            self._hist = self._hist[-_HIST_MAX:]
        self._hist_head = snap
        self._redo_stack.clear()
        self._refresh_hist_btns()

    def _apply_snap(self, snap: dict):
        self._hist_applying = True
        self._loading = True
        try:
            specs = self._specs_from_saved(snap if isinstance(snap, dict) else {})
            self._adopt_specs(specs)
            self._set_side_values("orig", snap.get("orig") if isinstance(snap, dict) else {})
            self._set_side_values("new", snap.get("new") if isinstance(snap, dict) else {})
            self._frozen_keys = self._parse_key_set(
                snap.get("frozen_fields") if isinstance(snap, dict) else None
            )
            for k in self._spec_keys():
                self._apply_freeze_visual(k)
            self._fit_all_edits()
            cb = getattr(self, "_prefs_dirty_cb", None)
            if callable(cb):
                try:
                    cb()
                except Exception:
                    log.exception("撤销/重做后保存提示词失败")
        finally:
            self._loading = False
            self._hist_applying = False

    def _undo(self):
        self._save_timer.stop()
        self._commit_hist()
        if not self._hist:
            return
        self._redo_stack.append(self._hist_head)
        self._hist_head = self._hist.pop()
        self._apply_snap(self._hist_head)
        self._refresh_hist_btns()

    def _redo(self):
        self._save_timer.stop()
        self._commit_hist()
        if not self._redo_stack:
            return
        self._hist.append(self._hist_head)
        if len(self._hist) > _HIST_MAX:
            self._hist = self._hist[-_HIST_MAX:]
        self._hist_head = self._redo_stack.pop()
        self._apply_snap(self._hist_head)
        self._refresh_hist_btns()

    def _side_values(self, side: str) -> Dict[str, str]:
        edits = self._left if side == "orig" else self._right
        return {k: ed.toPlainText().strip() for k, ed in edits.items()}

    def _set_side_values(self, side: str, data: Dict[str, str]):
        edits = self._left if side == "orig" else self._right
        tint = "blue" if side == "orig" else "green"
        data = _migrate_side(data or {}, list(edits.keys()))
        for k, ed in edits.items():
            ed.blockSignals(True)
            ed.setPlainText(str(data.get(k) or ""))
            ed.blockSignals(False)
            self._style_one_edit(ed, tint)

    def set_prefs_dirty_callback(self, cb):
        self._prefs_dirty_cb = cb

    @staticmethod
    def _import_char_leftovers(orig: dict, custom_presets: dict, char_data) -> Tuple[dict, dict]:
        """把角色模板残留的装备/场景细节/色彩并进提示词（仅空栏才填）。"""
        orig = dict(orig or {})
        custom_presets = dict(custom_presets or {})
        if not isinstance(char_data, dict):
            return orig, custom_presets
        keys = ("accessories", "scene_detail", "palette")
        fields = char_data.get("fields") if isinstance(char_data.get("fields"), dict) else {}
        for k in keys:
            if (orig.get(k) or "").strip():
                continue
            val = str((fields or {}).get(k) or "").strip()
            if val:
                orig[k] = val
        custom_in = (
            char_data.get("custom_modules")
            if isinstance(char_data.get("custom_modules"), dict)
            else {}
        )
        for k in keys:
            items = custom_in.get(k) if isinstance(custom_in, dict) else None
            if not isinstance(items, list):
                continue
            dest = list(custom_presets.get(k) or [])
            have = {
                (
                    str((it or {}).get("name") or "").strip(),
                    str((it or {}).get("value") or "").strip(),
                )
                for it in dest
            }
            for it in items:
                if not isinstance(it, dict):
                    continue
                name = str(it.get("name") or "").strip()
                value = str(it.get("value") or "").strip()
                if not name or not value or (name, value) in have:
                    continue
                dest.append({"name": name, "value": value})
                have.add((name, value))
            if dest:
                custom_presets[k] = dest
        return orig, custom_presets

    def export_settings(self) -> dict:
        return {
            "orig": self._side_values("orig"),
            "new": self._side_values("new"),
            "field_order": self._spec_keys(),
            "custom_fields": self._export_custom(),
            "field_titles": {k: t for k, t, _p in self._specs},
            "custom_presets": self._export_custom_presets(),
            "frozen_fields": sorted(self._frozen_keys),
        }

    def apply_settings(self, d: dict, char_data: dict = None, migrate_legacy_file: bool = True):
        data = d if isinstance(d, dict) else {}
        orig = data.get("orig") if isinstance(data.get("orig"), dict) else {}
        new = data.get("new") if isinstance(data.get("new"), dict) else {}
        # user.txt 还没有内容时，把旧的 editor.json 迁过来一次。
        # 新建的空白分页不能读这份旧文件，否则每页都会被灌进同一份正文。
        if migrate_legacy_file and not any(orig.values()) and not any(new.values()):
            legacy = self._read_legacy_file()
            if legacy:
                orig = legacy.get("orig") if isinstance(legacy.get("orig"), dict) else orig
                new = legacy.get("new") if isinstance(legacy.get("new"), dict) else new
                if not data.get("field_order"):
                    data = dict(data)
                    if isinstance(legacy.get("field_order"), list):
                        data["field_order"] = legacy.get("field_order")
                    if isinstance(legacy.get("custom_fields"), list):
                        data["custom_fields"] = legacy.get("custom_fields")
        self._custom_presets = self._parse_custom_presets(data)
        orig, self._custom_presets = self._import_char_leftovers(
            orig, self._custom_presets, char_data
        )
        self._frozen_keys = self._parse_key_set(data.get("frozen_fields"))
        self._loading = True
        try:
            specs = self._specs_from_saved(data)
            self._adopt_specs(specs)
            self._set_side_values("orig", orig)
            self._set_side_values("new", new)
        finally:
            self._loading = False
        for key in self._spec_keys():
            self._rebuild_field_chips(key)
            self._apply_freeze_visual(key)
        self._reset_hist()
        QTimer.singleShot(0, self._fit_all_edits)

    def _apply_section_widths(self, key: str):
        if key == "full":
            self._sync_full_prompt_layout()
            return
        wrap = self._sections.get(key)
        bar = self._title_bar_by_key.get(key)
        left = self._left.get(key)
        slot = self._right_slots.get(key)
        if wrap is None or bar is None or left is None or slot is None:
            return
        total = wrap.width()
        if total <= 0:
            return
        gap = 6
        title_w = bar.width()
        avail = max(1, total - title_w - 2 * gap)
        content_w = max(1, int(round(avail * _LEFT_RATIO)))
        rest = max(0, avail - content_w)
        if left.width() != content_w:
            left.setFixedWidth(content_w)
        if slot.width() != rest:
            slot.setFixedWidth(rest)
        pane = self._preset_panes.get(key)
        if pane is not None:
            try:
                pane.updateGeometry()
            except Exception:
                pass
        self._sync_section_row(key)

    @staticmethod
    def _parse_key_set(raw) -> set:
        if not isinstance(raw, (list, tuple, set)):
            return set()
        return {str(x) for x in raw if str(x).strip()}

    def _title_natural_h(self, lab: _VTitle) -> int:
        n = max(1, len(getattr(lab, "_chars", []) or []))
        # 上下边距 + 冻结按钮 + 两处 spacing（冻结、标题、弹簧）
        chrome = 3 + 3 + _TITLE_ACT + _TITLE_COL_GAP * 2
        return n * _title_line_height() + chrome

    def _sync_section_row(self, key: str):
        """竖排标题与左栏同高；右栏空位跟高。"""
        if getattr(self, "_syncing_row", False):
            return
        edit = self._left.get(key)
        bar = self._title_bar_by_key.get(key)
        if edit is None or bar is None:
            return
        self._syncing_row = True
        try:
            lab = self._title_labs.get(key)
            title_h = self._title_natural_h(lab) if lab is not None else 0
            h = max(int(edit.needed_height()), int(title_h))
            flow = self._preset_flows.get(key)
            pane = self._preset_panes.get(key)
            if flow is not None:
                pw = 0
                slot = self._right_slots.get(key)
                if slot is not None and 0 < slot.minimumWidth() == slot.maximumWidth():
                    # 右栏宽度刚由 _apply_section_widths 定死；内部 pane 可能还没重新布局，以定宽为准
                    pw = slot.maximumWidth() - 2 * _PRESET_PAD
                if pw <= 0 and pane is not None and pane.width() > 0:
                    pw = pane.width() - 2 * _PRESET_PAD
                if pw <= 0 and slot is not None:
                    pw = slot.width() - 2 * _PRESET_PAD
                if pw > 0:
                    need = int(flow.heightForWidth(pw))
                    h3f = getattr(self, "_h3_flows", {}).get(key)
                    h3h = h3f.parentWidget() if h3f is not None else None
                    if h3h is not None and not h3h.isHidden():
                        need += 6 + int(h3f.heightForWidth(pw))  # 6 = pane 竖排间距
                    h = max(h, need + 2 * _PRESET_PAD)
            edit.apply_height(h)
            if bar.minimumHeight() != h or bar.maximumHeight() != h:
                bar.setFixedHeight(h)
            slot = self._right_slots.get(key)
            if slot is not None:
                slot.setMinimumHeight(h)
            self._place_freeze_veil(key)
        finally:
            self._syncing_row = False

    def _fit_all_edits(self):
        for key in list(self._left.keys()):
            self._apply_section_widths(key)
            self._sync_section_row(key)
        self._refresh_full_prompt()
        self._sync_full_prompt_layout()

    def _schedule_persist(self):
        if self._loading:
            return
        self._save_timer.start()
        self._refresh_full_prompt()

    def _persist_now(self):
        if not self._hist_applying and not self._loading:
            self._commit_hist()
        cb = getattr(self, "_prefs_dirty_cb", None)
        if callable(cb):
            try:
                cb()
            except Exception:
                log.exception("通知 user.txt 保存提示词失败")

    @staticmethod
    def _read_legacy_file() -> dict:
        for path in _legacy_editor_paths():
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    return raw
            except Exception:
                log.exception("读取旧提示词文件失败")
        return {}


class PagePromptEditor(QWidget):
    """提示词分页容器。样式对齐图集下载的页签；每页正文和预设单独保存。"""

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
        self.setObjectName("PageRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._loading = False
        self._prefs_dirty_cb = None
        self._shared_presets: Dict[str, List[dict]] = {}
        # 各字段 H3 预设是否展开、预设 MRU 顺序，各分页共用一份
        self._h3_expanded: Dict[str, bool] = {}
        self._preset_mru: Dict[str, List[str]] = {}
        self._tab_rename_index = -1
        self._tab_filter_on = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("VideoTabWidget")
        self.tabs.setDocumentMode(False)
        self.tabs.setMovable(False)
        bar = self.tabs.tabBar()
        bar.setExpanding(False)
        bar.setElideMode(Qt.ElideNone)
        bar.setUsesScrollButtons(True)
        bar.setContextMenuPolicy(Qt.CustomContextMenu)
        bar.customContextMenuRequested.connect(self._on_tab_menu)
        bar.installEventFilter(self)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._tab_editor = QLineEdit(bar)
        self._tab_editor.setObjectName("PromptTabRename")
        self._tab_editor.setFrame(False)
        self._tab_editor.setAlignment(Qt.AlignCenter)
        self._tab_editor.hide()
        self._tab_editor.returnPressed.connect(self._commit_tab_rename)
        self._tab_editor.installEventFilter(self)
        self._style_tab_editor()
        root.addWidget(self.tabs, 1)

        corner = QWidget()
        corner.setObjectName("PromptTabCorner")
        corner.setStyleSheet("#PromptTabCorner{background:transparent;border:none;}")
        corner_lay = QHBoxLayout(corner)
        # 底边留 2px，让两个按钮的下边线外面有空隙
        corner_lay.setContentsMargins(0, 0, 8, 2)
        corner_lay.setSpacing(4)
        corner_lay.setAlignment(Qt.AlignBottom)
        self.btn_add_page = self._make_page_corner_btn(
            "+", "添加分页。正文按页分开，预设各页共用。当前页再点一次改名。"
        )
        self.btn_del_page = self._make_page_corner_btn("-", "删除当前分页")
        self.btn_add_page.clicked.connect(self._add_page)
        self.btn_del_page.clicked.connect(self._delete_current_page)
        corner_lay.addWidget(self.btn_add_page)
        corner_lay.addWidget(self.btn_del_page)
        self.tabs.setCornerWidget(corner, Qt.TopRightCorner)

        self.setStyleSheet(self._tab_qss())
        self._style_page_corner_btns()
        self._refresh_page_corner()
        try:
            theme.changed.connect(self.restyle_theme)
        except Exception:
            pass

    @staticmethod
    def _tab_qss() -> str:
        return fmt(TAB_QSS + VIDEO_TAB_QSS)

    _PAGE_CORNER_BTN = 24

    def _make_page_corner_btn(self, text: str, tip: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedSize(self._PAGE_CORNER_BTN, self._PAGE_CORNER_BTN)
        btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        btn.setToolTip(tip)
        return btn

    def _style_page_corner_btns(self):
        h = _PromptSheet._hot_colors()
        n = self._PAGE_CORNER_BTN
        # 样式里的宽高是内容区，1px 边框会再加 2px，这里扣掉才是外沿 24
        inner = n - 2
        qss = (
            "QPushButton{"
            f"min-width:{inner}px;max-width:{inner}px;min-height:{inner}px;max-height:{inner}px;"
            "padding:0;border-radius:6px;"
            f"border:1px solid {tk('border')};"
            f"background:{tk('input_bg')};color:{tk('text')};"
            "font-size:15px;font-weight:700;}"
            "QPushButton:hover{"
            f"background:{h['hover_bg']};border-color:{h['hover_bd']};color:{h['hover_fg']};}}"
            "QPushButton:pressed{"
            f"background:{h['press_bg']};border-color:{h['press_bd']};color:{h['press_fg']};}}"
            "QPushButton:disabled{"
            f"background:{tk('accent_dis')};border-color:{tk('border')};"
            f"color:{tk('text_dim')};}}"
        )
        for btn in (
            getattr(self, "btn_add_page", None),
            getattr(self, "btn_del_page", None),
        ):
            if btn is not None:
                btn.setStyleSheet(qss)
                btn.setFixedSize(n, n)

    def _refresh_page_corner(self):
        btn = getattr(self, "btn_del_page", None)
        if btn is not None:
            btn.setEnabled(self.tabs.count() > 1)

    def _delete_current_page(self):
        self._cancel_tab_rename()
        self._delete_tab_at(self.tabs.currentIndex())

    def _style_tab_editor(self):
        ed = getattr(self, "_tab_editor", None)
        if ed is None:
            return
        # 实心底盖住页签原文字，避免改名时重影；不要外框
        ed.setAttribute(Qt.WA_StyledBackground, True)
        ed.setAutoFillBackground(True)
        bg = tk("panel_2")
        pal = ed.palette()
        pal.setColor(ed.backgroundRole(), QColor(bg))
        ed.setPalette(pal)
        ed.setStyleSheet(
            "QLineEdit#PromptTabRename{"
            f"background:{bg};color:{tk('text_strong')};"
            "border:none;padding:0 4px;font-size:14px;font-weight:600;}"
        )

    def restyle_theme(self, *_):
        self.setStyleSheet(self._tab_qss())
        self._style_page_corner_btns()
        self._style_tab_editor()
        self._refresh_tab_chrome()
        self._place_tab_editor()

    def _refresh_tab_chrome(self):
        idx = self.tabs.currentIndex()
        first = "true" if idx <= 0 else "false"
        self.tabs.setProperty("firstSelected", first)
        st = self.tabs.style()
        if st is not None:
            st.unpolish(self.tabs)
            st.polish(self.tabs)
        self.tabs.update()

    def _used_ids(self) -> set:
        used = set()
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            pid = str(getattr(w, "_page_id", "") or "").strip()
            if pid:
                used.add(pid)
        return used

    def _sheets(self):
        out = []
        tabs = getattr(self, "tabs", None)
        if tabs is None:
            return out
        for i in range(tabs.count()):
            w = tabs.widget(i)
            if isinstance(w, _PromptSheet):
                out.append(w)
        return out

    def _rebind_shared_presets(self):
        """每页的绿色预设都指向同一份，改一页其余页一起能看见。"""
        shared = self._shared_presets
        for sheet in self._sheets():
            sheet._preset_host = self
            sheet._custom_presets = shared
            for key in list(sheet._spec_keys()):
                if key in getattr(sheet, "_preset_flows", {}):
                    sheet._rebuild_field_chips(key)
            QTimer.singleShot(0, sheet._fit_all_edits)

    def sync_shared_presets(self):
        if self._loading:
            return
        self._rebind_shared_presets()
        self._schedule_persist()

    def _mount_sheet(self, sheet: _PromptSheet, title: str):
        sheet._preset_host = self
        sheet._custom_presets = self._shared_presets
        sheet._h3_expanded = self._h3_expanded
        sheet._preset_mru = self._preset_mru
        sheet.set_prefs_dirty_callback(self._schedule_persist)
        pid = str(getattr(sheet, "_page_id", "") or "page")
        apply_transparent_surface(sheet, "PromptSheet_" + pid)
        self.tabs.addTab(sheet, title)
        idx = self.tabs.indexOf(sheet)
        if idx >= 0:
            self.tabs.setTabToolTip(idx, "点一次切换，当前页再点一次改名")
        for key in list(sheet._spec_keys()):
            if key in getattr(sheet, "_preset_flows", {}):
                sheet._rebuild_field_chips(key)

    def _discard_sheets(self):
        while self.tabs.count():
            w = self.tabs.widget(0)
            self.tabs.removeTab(0)
            if w is None:
                continue
            try:
                w._teardown_click_filter()
            except Exception:
                pass
            w.setParent(None)
            w.deleteLater()

    def _select_id(self, page_id: str):
        want = str(page_id or "").strip()
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if w is not None and str(getattr(w, "_page_id", "") or "") == want:
                self.tabs.setCurrentIndex(i)
                return
        if self.tabs.count():
            self.tabs.setCurrentIndex(0)

    def _enter_current(self):
        w = self.tabs.currentWidget()
        if w is None:
            return
        try:
            w.on_enter()
        except RuntimeError:
            pass

    def _on_tab_changed(self, _index: int):
        self._refresh_tab_chrome()
        if self._loading:
            return
        self._schedule_persist()
        QTimer.singleShot(0, self._enter_current)

    def _schedule_persist(self):
        if self._loading:
            return
        cb = getattr(self, "_prefs_dirty_cb", None)
        if callable(cb):
            try:
                cb()
            except Exception:
                log.exception("通知 user.txt 保存提示词分页失败")

    def set_prefs_dirty_callback(self, cb):
        self._prefs_dirty_cb = cb

    def on_enter(self):
        self._enter_current()

    def _add_page(self):
        n = self.tabs.count() + 1
        suggest = f"分类{n}"
        name, ok = QInputDialog.getText(self, "添加分页", "分页名称：", text=suggest)
        if not ok:
            return
        self._add_page_named(_clean_page_title(name, suggest))

    def _add_page_named(self, title: str) -> _PromptSheet:
        title = _clean_page_title(title, f"分类{self.tabs.count() + 1}")
        sheet = _PromptSheet()
        sheet._page_id = _fresh_page_id(self._used_ids())
        self._mount_sheet(sheet, title)
        self.tabs.setCurrentWidget(sheet)
        self._refresh_tab_chrome()
        self._refresh_page_corner()
        self._schedule_persist()
        QTimer.singleShot(0, self._enter_current)
        return sheet

    def _install_tab_filter(self):
        if self._tab_filter_on:
            return
        app = QApplication.instance()
        if app is None:
            return
        app.installEventFilter(self)
        self._tab_filter_on = True

    def _remove_tab_filter(self):
        if not self._tab_filter_on:
            return
        app = QApplication.instance()
        if app is not None:
            try:
                app.removeEventFilter(self)
            except Exception:
                pass
        self._tab_filter_on = False

    def _begin_tab_rename(self, index: int):
        """点一下页签名就地改名。回车或点空白处结束。"""
        if self._loading:
            return
        if index < 0 or index >= self.tabs.count():
            return
        if self._tab_rename_index == index and not self._tab_editor.isHidden():
            return
        if self._tab_rename_index >= 0:
            self._commit_tab_rename()
        bar = self.tabs.tabBar()
        self._tab_rename_index = index
        self._tab_editor.setText(self.tabs.tabText(index))
        self._tab_editor.show()
        self._tab_editor.raise_()
        self._place_tab_editor()
        self._install_tab_filter()
        self._tab_editor.setFocus()
        self._tab_editor.setCursorPosition(len(self._tab_editor.text() or ""))
        del bar

    def _place_tab_editor(self):
        ed = getattr(self, "_tab_editor", None)
        idx = getattr(self, "_tab_rename_index", -1)
        if ed is None or idx < 0:
            return
        bar = self.tabs.tabBar()
        if idx < 0 or idx >= bar.count():
            return
        rect = bar.tabRect(idx)
        if not rect.isValid() or rect.isEmpty():
            return
        # 铺满页签内部，盖住原来的标题，避免重影
        ed.setGeometry(rect.adjusted(1, 1, -1, -1))

    def _commit_tab_rename(self):
        idx = getattr(self, "_tab_rename_index", -1)
        if idx < 0:
            return
        self._tab_rename_index = -1
        text = ""
        ed = getattr(self, "_tab_editor", None)
        if ed is not None:
            text = ed.text() or ""
            ed.hide()
        self._remove_tab_filter()
        if idx >= self.tabs.count():
            return
        title = _clean_page_title(text, "")
        cur = self.tabs.tabText(idx)
        if not title or title == cur:
            return
        self.tabs.setTabText(idx, title)
        self.tabs.setTabToolTip(idx, "点一次切换，当前页再点一次改名")
        self._schedule_persist()

    def _cancel_tab_rename(self):
        self._tab_rename_index = -1
        ed = getattr(self, "_tab_editor", None)
        if ed is not None:
            ed.hide()
        self._remove_tab_filter()

    def eventFilter(self, obj, ev):
        bar = self.tabs.tabBar() if getattr(self, "tabs", None) is not None else None
        if obj is bar and ev.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease) and ev.button() == Qt.LeftButton:
            # 按下时当前页还没切走：只有点在已经打开的这一页上，才进入改名
            if ev.type() == QEvent.MouseButtonPress:
                idx = bar.tabAt(ev.pos())
                self._tab_arm_index = idx
                self._tab_arm_rename = idx >= 0 and idx == self.tabs.currentIndex()
            elif ev.type() == QEvent.MouseButtonRelease:
                idx = bar.tabAt(ev.pos())
                armed = bool(getattr(self, "_tab_arm_rename", False))
                arm_i = getattr(self, "_tab_arm_index", -2)
                self._tab_arm_rename = False
                if armed and idx == arm_i and idx >= 0:
                    QTimer.singleShot(0, lambda i=idx: self._begin_tab_rename(i))
        ed = getattr(self, "_tab_editor", None)
        if ed is not None and obj is ed and ev.type() == QEvent.KeyPress:
            if ev.key() == Qt.Key_Escape:
                self._cancel_tab_rename()
                return True
        if (
            getattr(self, "_tab_rename_index", -1) >= 0
            and ed is not None
            and not ed.isHidden()
            and ev.type() == QEvent.MouseButtonPress
        ):
            inside = obj is ed
            gp = ev.globalPos() if hasattr(ev, "globalPos") else None
            if gp is not None and not inside:
                inside = ed.rect().contains(ed.mapFromGlobal(gp))
            if not inside:
                self._commit_tab_rename()
        return False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_tab_editor()

    def _on_tab_menu(self, pos):
        bar = self.tabs.tabBar()
        index = bar.tabAt(pos)
        if index < 0:
            return
        menu = QMenu(self)
        act_rename = menu.addAction("改名")
        act_del = menu.addAction("删除")
        act_del.setEnabled(self.tabs.count() > 1)
        chosen = menu.exec_(bar.mapToGlobal(pos))
        if chosen is act_rename:
            self._begin_tab_rename(index)
        elif chosen is act_del:
            self._delete_tab_at(index)

    def _delete_tab_at(self, index: int, confirm: bool = True):
        if index < 0 or index >= self.tabs.count():
            return
        if self.tabs.count() <= 1:
            return
        if getattr(self, "_tab_rename_index", -1) == index:
            self._cancel_tab_rename()
        title = self.tabs.tabText(index) or "这一页"
        if confirm and not _confirm_delete_page(self, title):
            return
        w = self.tabs.widget(index)
        self.tabs.removeTab(index)
        if w is not None:
            try:
                w._teardown_click_filter()
            except Exception:
                pass
            w.setParent(None)
            w.deleteLater()
        self._refresh_tab_chrome()
        self._refresh_page_corner()
        self._schedule_persist()
        QTimer.singleShot(0, self._enter_current)

    def export_settings(self) -> dict:
        pages = []
        active = ""
        current = self.tabs.currentIndex()
        for i in range(self.tabs.count()):
            sheet = self.tabs.widget(i)
            if sheet is None or not hasattr(sheet, "export_settings"):
                continue
            item = sheet.export_settings()
            pid = str(getattr(sheet, "_page_id", "") or "").strip() or _fresh_page_id(
                {p.get("id") for p in pages}
            )
            sheet._page_id = pid
            item["id"] = pid
            item["title"] = _clean_page_title(self.tabs.tabText(i), f"分类{i + 1}")
            # 预设不跟某一页走，统一写在外面
            item.pop("custom_presets", None)
            pages.append(item)
            if i == current:
                active = pid
        if not pages:
            pages = [_blank_prompt_page("p1", "默认")]
            active = "p1"
        if not active:
            active = pages[0]["id"]
        shared = {}
        probe = self._sheets()
        if probe:
            probe[0]._custom_presets = self._shared_presets
            shared = probe[0]._export_custom_presets()
        h3 = {k: True for k in sorted(self._h3_expanded) if self._h3_expanded.get(k)}
        return {
            "active": active, "pages": pages, "custom_presets": shared,
            "h3_expanded": h3,
            "preset_mru": {
                k: [str(v) for v in lst][:_MRU_MAX]
                for k, lst in sorted(self._preset_mru.items()) if lst
            },
        }

    def apply_settings(self, d: dict, char_data: dict = None):
        data = d if isinstance(d, dict) else {}
        pages, active, migrate_file = prompt_pages_from_store(data)
        raw = data.get("pages")
        has_saved_pages = (
            isinstance(raw, list)
            and any(isinstance(p, dict) and _prompt_dict_has_body(p) for p in raw)
        )
        legacy_flat = (not has_saved_pages) and _prompt_dict_has_body(data)
        if legacy_flat:
            log.info("提示词旧数据已收进「默认」分页")
        use_char = bool(char_data) and (legacy_flat or migrate_file)
        self._cancel_tab_rename()
        self._shared_presets = _merge_preset_maps(
            _PromptSheet._parse_custom_presets(data),
            {},
        )
        for item in pages:
            self._shared_presets = _merge_preset_maps(
                self._shared_presets,
                _PromptSheet._parse_custom_presets(item),
            )
        raw_h3 = data.get("h3_expanded")
        self._h3_expanded.clear()
        if isinstance(raw_h3, dict):
            for hk, hv in raw_h3.items():
                hk = str(hk or "").strip()
                if hk and hv:
                    self._h3_expanded[hk] = True
        raw_mru = data.get("preset_mru")
        self._preset_mru.clear()
        if isinstance(raw_mru, dict):
            for mk, mv in raw_mru.items():
                mk = str(mk or "").strip()
                if not mk or not isinstance(mv, list):
                    continue
                vals: List[str] = []
                seen_v = set()
                for v in mv:
                    v = str(v or "").strip()
                    if v and v not in seen_v:
                        seen_v.add(v)
                        vals.append(v)
                if vals:
                    self._preset_mru[mk] = vals[:_MRU_MAX]
        self._loading = True
        self.tabs.blockSignals(True)
        try:
            self._discard_sheets()
            for i, item in enumerate(pages):
                sheet = _PromptSheet()
                sheet._page_id = item.get("id") or _fresh_page_id(self._used_ids())
                sheet._preset_host = self
                sheet._h3_expanded = self._h3_expanded
                sheet._preset_mru = self._preset_mru
                sheet.apply_settings(
                    item,
                    char_data if (i == 0 and use_char) else None,
                    migrate_legacy_file=bool(migrate_file and i == 0),
                )
                if i == 0:
                    self._shared_presets = _merge_preset_maps(
                        self._shared_presets, sheet._custom_presets
                    )
                self._mount_sheet(sheet, item.get("title") or "默认")
            if self.tabs.count() <= 0:
                sheet = _PromptSheet()
                sheet._page_id = "p1"
                self._mount_sheet(sheet, "默认")
            self._select_id(active)
        finally:
            self.tabs.blockSignals(False)
            self._loading = False
            self._refresh_tab_chrome()
            self._refresh_page_corner()
            QTimer.singleShot(0, self._enter_current)
