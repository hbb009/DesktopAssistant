# pages/page_prompt_editor.py
# 文生视频提示词编辑：顶部分页（每页正文 / 预设分开存），
# 页内左栏填写（40%）/ 右栏预设（60%）。字段标题一字宽竖排，贴在内容左侧。

from __future__ import annotations

import json
import math
import os
import random
import re
from typing import Dict, List, Tuple

from PyQt5.QtCore import (
    Qt, QTimer, QSize, QRectF, QPointF, QPoint, QRect, QEvent,
    QPropertyAnimation, QEasingCurve, QAbstractAnimation, QParallelAnimationGroup,
)
from PyQt5.QtGui import (
    QColor, QPalette, QTextDocument, QIcon, QPixmap, QPainter, QPen, QFont,
    QFontMetrics, QTextCursor, QPainterPath, QBrush,
    QTextCharFormat, QTextBlockFormat,
)
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit,
    QScrollArea, QFrame, QSizePolicy, QApplication, QInputDialog, QMenu,
    QButtonGroup, QDialog,
    QLineEdit, QTabWidget, QMessageBox, QComboBox, QCheckBox,
)

from styles.style_all import (
    theme, tk, fmt, apply_mini_button,
    TAB_QSS, VIDEO_TAB_QSS, apply_transparent_surface,
    set_message_box_selectable, message_box_warn,
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
        ("法师", "a female arcane sorceress"),
        ("网球教练", "a female professional tennis coach"),
        ("航天工程师", "a female aerospace engineer"),
        ("厨师", "a female chef"),
        ("建筑师", "a female architect"),
        ("摄影师", "a female wildlife photographer"),
        ("指挥家", "a female orchestra conductor"),
        ("机械师", "a female aerospace mechanic"),
        ("警探", "a female police detective"),
        ("科学家", "a female scientist"),
        ("兽医", "a female veterinarian"),
        ("空乘", "a female flight attendant"),
        ("舞蹈教练", "a female dance instructor"),
        ("律师", "a female lawyer"),
        ("外科医生", "a female surgeon"),
        ("记者", "a female investigative journalist"),
        ("武术教练", "a female martial arts instructor"),
        ("调酒师", "a female bartender"),
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

# ===== 预设 txt：docs/提示词分类.txt（单层【分类】+ 序号. 标题 | 内容），启动时读一次 =====
# （旧版双文件 / ## 内置|H3 标记仍兼容：加载时合并为一份列表；读不到则回退 _CHAR_PRESETS）
# UI：每栏全部条目按内容去重；前 _TOP_N 条直接显示，其余折进蓝色「H3 ▸」溢出区（≤_TOP_N 不显示 H3）
_PRESET_TXT_MERGED = "提示词分类.txt"
_PRESET_TXT_BUILTIN = "内置预设_提示词分类.txt"  # legacy fallback
_PRESET_TXT_H3 = "MiniMAX_H3_提示词分类.txt"  # legacy fallback
_PRESET_LAYER_RE = re.compile(r"^##\s*(内置|H3)\s*$")  # legacy markers ignored as section breaks
# 条目顺序以 docs/提示词分类.txt 各分类内序号为准（点 chip 写入时将该条提到本栏 1.）
_TOP_N = 15
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
    """解析预设 txt → {字段key: [(标题, 内容), ...]}；文件读不了返回 None。

    单层格式：【分类】+ 序号. 标题 | 内容。
    兼容旧「## 内置 / ## H3」标记：当作分段忽略，条目全部收入同一列表。
    空行与 # 注释跳过；乱码 / 未知分类 / 缺「 | 」的行跳过并记日志。
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
        # Legacy ## 内置 / ## H3 — ignore as section break (single-list load)
        if _PRESET_LAYER_RE.match(s):
            cur = None
            continue
        if s.startswith("#"):
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


def _merge_preset_dicts(*dicts):
    """Concatenate preset dicts in order; drop duplicate (name, value) per field."""
    keys = list(dict.fromkeys(_PRESET_TXT_CATS.values()))
    out: Dict[str, List[Tuple[str, str]]] = {k: [] for k in keys}
    seen: Dict[str, set] = {k: set() for k in keys}
    for d in dicts:
        if not d:
            continue
        for k in keys:
            for name, value in d.get(k) or []:
                pair = (name, value)
                if pair in seen[k]:
                    continue
                seen[k].add(pair)
                out[k].append((name, value))
    return out


def _load_all_presets():
    """Load single-layer merged file, else concat legacy dual files into one list."""
    merged = _preset_txt_path(_PRESET_TXT_MERGED)
    if merged:
        parsed = _parse_preset_txt(merged)
        if parsed is not None:
            return merged, parsed
    path_b = _preset_txt_path(_PRESET_TXT_BUILTIN)
    path_h = _preset_txt_path(_PRESET_TXT_H3)
    parts = []
    paths = []
    for path in (path_b, path_h):
        if not path:
            continue
        parsed = _parse_preset_txt(path)
        if parsed is not None:
            parts.append(parsed)
            paths.append(path)
    if not parts:
        return "", None
    return "+".join(paths), _merge_preset_dicts(*parts)


def _txt_presets(reload: bool = False):
    """单层预设 dict {字段key: [(标题, 内容), ...]}；None 表示回退代码里的 _CHAR_PRESETS。"""
    if reload:
        _PRESET_TXT_CACHE.clear()
    data = _PRESET_TXT_CACHE.get("data")
    if data is not None:
        return data
    path, parsed = _load_all_presets()
    if parsed is not None and any(parsed.values()):
        total = sum(len(v) for v in parsed.values())
        over = sum(1 for v in parsed.values() if len(v) > _TOP_N)
        log.info(
            "提示词预设 txt：共 %d 条（%d 栏需 H3 溢出），path=%s",
            total, over, path or "?",
        )
        data = parsed
    else:
        if path:
            log.warning("预设 txt 无可用条目，改用代码内置预设：%s", path)
        else:
            log.info("未找到 %s（及旧双文件），改用代码内置预设", _PRESET_TXT_MERGED)
        data = None
    _PRESET_TXT_CACHE["data"] = data
    return data



def _txt_cat_labels_for_key(field_key: str) -> List[str]:
    """字段 key → 文件里可能出现的【分类】中文名（含别名）。"""
    return [cn for cn, k in _PRESET_TXT_CATS.items() if k == field_key]


def _promote_txt_preset_entry(field_key: str, value: str, name: str = "") -> bool:
    """把指定条目移到该分类第 1 条并重写 docs/提示词分类.txt；已在最前返回 False。

    只改目标【分类】段内的「序号. 标题 | 内容」行，其它分类与文件头注释不动。
    成功后刷新 _PRESET_TXT_CACHE，之后 _txt_presets() / UI 顺序都跟文件一致。
    """
    value = (value or "").strip()
    name = (name or "").strip()
    if not field_key or not value:
        return False
    path = _preset_txt_path(_PRESET_TXT_MERGED)
    if not path:
        log.warning("提升预设顺序失败：找不到 %s", _PRESET_TXT_MERGED)
        return False
    labels = set(_txt_cat_labels_for_key(field_key))
    if not labels:
        return False
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception:
        log.exception("读取预设 txt 失败：%s", path)
        return False
    text0 = raw.decode("utf-8-sig", errors="replace")
    newline = "\r\n" if "\r\n" in text0 else "\n"
    lines = text0.splitlines()
    sec_start = None
    sec_end = None
    for i, line in enumerate(lines):
        s = line.replace("\ufeff", "").strip()
        m = _PRESET_TXT_HEAD_RE.match(s)
        if not m:
            continue
        label = m.group(1)
        if sec_start is not None:
            sec_end = i
            break
        if label in labels or _PRESET_TXT_CATS.get(label) == field_key:
            sec_start = i
    if sec_start is None:
        log.warning("提升预设顺序失败：文件中无分类 %s", field_key)
        return False
    if sec_end is None:
        sec_end = len(lines)

    items: List[Tuple[int, str, str]] = []  # (line_idx, title, body)
    for i in range(sec_start + 1, sec_end):
        s = lines[i].replace("\ufeff", "").strip()
        if not s or s.startswith("#") or _PRESET_LAYER_RE.match(s):
            continue
        m = _PRESET_TXT_ITEM_RE.match(s)
        if not m:
            continue
        body = m.group(1)
        sep = " | " if " | " in body else ("|" if "|" in body else "")
        if not sep:
            continue
        title, val = body.split(sep, 1)
        title, val = title.strip(), val.strip()
        if title and val:
            items.append((i, title, val))
    if not items:
        return False

    hit = None
    for j, (_li, title, val) in enumerate(items):
        if val == value and (not name or title == name):
            hit = j
            break
    if hit is None:
        for j, (_li, title, val) in enumerate(items):
            if val == value:
                hit = j
                break
    if hit is None:
        log.warning("提升预设顺序失败：分类 %s 中无匹配条目", field_key)
        return False
    if hit == 0:
        return False  # already first

    ordered = [items[hit]] + [it for j, it in enumerate(items) if j != hit]
    # Rewrite item lines in file order: first slot = promoted entry as 1., rest shift
    slots = sorted(li for li, _t, _v in items)
    for n, li, (_oli, title, val) in zip(range(1, len(ordered) + 1), slots, ordered):
        lines[li] = f"{n}. {title} | {val}"

    out = newline.join(lines)
    if text0.endswith(("\n", "\r\n")):
        out += newline
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(out)
        os.replace(tmp, path)
    except Exception:
        log.exception("写入预设 txt 失败：%s", path)
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False

    data = _PRESET_TXT_CACHE.get("data")
    if isinstance(data, dict):
        data[field_key] = [(t, v) for _li, t, v in ordered]
    else:
        _txt_presets(reload=True)
    log.info(
        "预设顺序已写入 %s：[%s] → 1. %s",
        os.path.basename(path), field_key, ordered[0][1],
    )
    return True


def _normalize_entry_text(name: str, value: str) -> Tuple[str, str, str]:
    """压成单行。标题里的 | 会拆坏「标题 | 内容」。返回 (标题, 内容, 错误)。"""
    name = " ".join((name or "").split())
    value = " ".join((value or "").split())
    if "|" in name:
        return "", "", "标题里不能有 |"
    if not name or not value:
        return "", "", "请填写标题和内容"
    return name, value, ""


def _splice_category_lines(lines: List[str], field_key: str, pairs: List[Tuple[str, str]]):
    """替换一个【分类】里的「序号. 标题 | 内容」行，其它行不动。

    找不到该分类的标题行时返回 None。pairs 是这一栏的新顺序，从 1 重新编号。
    """
    labels = set(_txt_cat_labels_for_key(field_key))
    if not labels:
        return None
    sec_start = None
    sec_end = None
    for i, line in enumerate(lines):
        s = line.replace("\ufeff", "").strip()
        m = _PRESET_TXT_HEAD_RE.match(s)
        if not m:
            continue
        if sec_start is not None:
            sec_end = i
            break
        label = m.group(1)
        if label in labels or _PRESET_TXT_CATS.get(label) == field_key:
            sec_start = i
    if sec_start is None:
        return None
    if sec_end is None:
        sec_end = len(lines)
    item_idxs = []
    for i in range(sec_start + 1, sec_end):
        s = lines[i].replace("\ufeff", "").strip()
        if _PRESET_TXT_ITEM_RE.match(s):
            item_idxs.append(i)
    out = list(lines)
    if item_idxs:
        insert_at = item_idxs[0]
        for i in reversed(item_idxs):
            del out[i]
    else:
        insert_at = sec_start + 1
    new_lines = [
        f"{n}. {title} | {val}"
        for n, (title, val) in enumerate(pairs, 1)
    ]
    for offset, line in enumerate(new_lines):
        out.insert(insert_at + offset, line)
    return out


def _read_txt_category(field_key: str):
    """读分类文件里这一栏的 (标题, 内容)。没有文件时返回 None。"""
    path = _preset_txt_path(_PRESET_TXT_MERGED)
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception:
        log.exception("读取预设 txt 失败：%s", path)
        return None
    lines = raw.decode("utf-8-sig", errors="replace").splitlines()
    labels = set(_txt_cat_labels_for_key(field_key))
    if not labels:
        return None
    sec_start = None
    sec_end = None
    for i, line in enumerate(lines):
        s = line.replace("\ufeff", "").strip()
        m = _PRESET_TXT_HEAD_RE.match(s)
        if not m:
            continue
        if sec_start is not None:
            sec_end = i
            break
        label = m.group(1)
        if label in labels or _PRESET_TXT_CATS.get(label) == field_key:
            sec_start = i
    if sec_start is None:
        return []
    if sec_end is None:
        sec_end = len(lines)
    out: List[Tuple[str, str]] = []
    for i in range(sec_start + 1, sec_end):
        s = lines[i].replace("\ufeff", "").strip()
        m = _PRESET_TXT_ITEM_RE.match(s)
        if not m:
            continue
        body = m.group(1)
        sep = " | " if " | " in body else ("|" if "|" in body else "")
        if not sep:
            continue
        title, val = body.split(sep, 1)
        title, val = title.strip(), val.strip()
        if title and val:
            out.append((title, val))
    return out


def _write_txt_category(field_key: str, pairs: List[Tuple[str, str]]) -> bool:
    """把这一栏的词条整段写回 docs/提示词分类.txt，并刷新内存缓存。"""
    path = _preset_txt_path(_PRESET_TXT_MERGED)
    if not path:
        log.warning("写入预设失败：找不到 %s", _PRESET_TXT_MERGED)
        return False
    labels = _txt_cat_labels_for_key(field_key)
    if not labels:
        return False
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception:
        log.exception("读取预设 txt 失败：%s", path)
        return False
    text0 = raw.decode("utf-8-sig", errors="replace")
    newline = "\r\n" if "\r\n" in text0 else "\n"
    lines = text0.splitlines()
    spliced = _splice_category_lines(lines, field_key, pairs)
    if spliced is None:
        block = [f"【{labels[0]}】"]
        block.extend(f"{n}. {title} | {val}" for n, (title, val) in enumerate(pairs, 1))
        base = text0
        if base and not base.endswith(("\n", "\r\n")):
            base += newline
        if base and not base.endswith(newline + newline):
            base += newline
        out = base + newline.join(block) + newline
    else:
        out = newline.join(spliced)
        if text0.endswith(("\n", "\r\n")):
            out += newline
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(out)
        os.replace(tmp, path)
    except Exception:
        log.exception("写入预设 txt 失败：%s", path)
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False
    data = _PRESET_TXT_CACHE.get("data")
    if isinstance(data, dict):
        data[field_key] = [(t, v) for t, v in pairs]
    else:
        _txt_presets(reload=True)
    log.info(
        "预设词条已写入 %s：[%s] %d 条",
        os.path.basename(path), field_key, len(pairs),
    )
    return True


def _confirm_delete_entry(parent, title: str) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("删除词条")
    shown = (title or "").strip() or "这条"
    box.setText(f"删除「{shown}」？")
    box.setInformativeText("会从这一栏的词条里去掉，左边正文里的这段也会清掉。")
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.setDefaultButton(QMessageBox.No)
    try:
        set_message_box_selectable(box)
    except Exception:
        pass
    return box.exec_() == QMessageBox.Yes


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
    """One-shot migrate candidate: userdata/prompts_editor.txt only."""
    paths = []
    try:
        from utils.app_paths import userdata_dir
        paths.append(os.path.join(userdata_dir(), "prompts_editor.txt"))
    except Exception:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        paths.append(os.path.join(here, "userdata", "prompts_editor.txt"))
    return paths


def _catalog_map() -> Dict[str, Tuple[str, str, str]]:
    return {k: (k, t, p) for k, t, p in _FIELDS}


_PAGE_TITLE_MAX = 24
_PAGE_SETTING_KEYS = (
    "orig", "new", "field_order", "custom_fields",
    "field_titles", "custom_presets", "frozen_fields", "locked_fields",
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
        "locked_fields": [],
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
    locked = data.get("locked_fields")
    if isinstance(locked, list) and locked:
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


_OPUS_MT_FOLDERS = (
    ("opus-mt-zh-en", "opus-mt-zh-en"),
    ("opus-mt-en-zh", "opus-mt-en-zh"),
)
_OPUS_MT_BUNDLE_ID = "opus-mt"
_OPUS_MT_BUNDLE_LABEL = "opus-MT 中英"


def _opus_mt_ready(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    if not os.path.isfile(os.path.join(path, "config.json")):
        return False
    for name in ("pytorch_model.bin", "model.safetensors"):
        if os.path.isfile(os.path.join(path, name)):
            return True
    try:
        for fn in os.listdir(path):
            fp = os.path.join(path, fn)
            if (
                os.path.isfile(fp)
                and fn.endswith((".bin", ".safetensors"))
                and os.path.getsize(fp) > 1_000_000
            ):
                return True
    except OSError:
        pass
    return False


_OPUS_MT_CACHE_T = 0.0
_OPUS_MT_CACHE_ITEMS: List[Tuple[str, str]] | None = None


def _opus_mt_bundle_ready(root: str = "") -> bool:
    """双向权重都在（或至少一侧）即视为 opus-MT 中英可用。"""
    if not root:
        try:
            from utils.app_paths import model_dir
            root = model_dir()
        except Exception:
            root = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "model"
            )
    return any(
        _opus_mt_ready(os.path.join(root, folder)) for folder, _ in _OPUS_MT_FOLDERS
    )


def _list_opus_mt_models(*, force: bool = False) -> List[Tuple[str, str]]:
    """逻辑模型列表：只返回一项 opus-MT 中英（覆盖双向）。5 秒缓存。"""
    import time
    global _OPUS_MT_CACHE_T, _OPUS_MT_CACHE_ITEMS
    now = time.monotonic()
    if (
        not force
        and _OPUS_MT_CACHE_ITEMS is not None
        and now - _OPUS_MT_CACHE_T < 5.0
    ):
        return list(_OPUS_MT_CACHE_ITEMS)
    out: List[Tuple[str, str]] = []
    if _opus_mt_bundle_ready():
        out.append((_OPUS_MT_BUNDLE_ID, _OPUS_MT_BUNDLE_LABEL))
    _OPUS_MT_CACHE_T = now
    _OPUS_MT_CACHE_ITEMS = list(out)
    return out




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
        # 闭眼弧线的墨迹中心对齐图标框，避免冻成一行时眼睛偏下
        cy = s * 0.43
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



def _pick_icon(kind: str, size: int, color: str) -> QIcon:
    """Footer pick icons: random=shuffle arrows, seq=lines+down arrow."""
    size = max(14, int(size))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    s = float(size)
    c = QColor(color)
    pen = QPen(c, max(1.4, s * 0.09))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    if kind == "seq":
        lx, rx = s * 0.14, s * 0.62
        for y in (s * 0.28, s * 0.50, s * 0.72):
            p.drawLine(QPointF(lx, y), QPointF(rx, y))
        ax = s * 0.78
        p.drawLine(QPointF(ax, s * 0.22), QPointF(ax, s * 0.70))
        tri = QPainterPath()
        tri.moveTo(ax, s * 0.82)
        tri.lineTo(ax - s * 0.12, s * 0.62)
        tri.lineTo(ax + s * 0.12, s * 0.62)
        tri.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(c))
        p.drawPath(tri)
    else:
        path1 = QPainterPath()
        path1.moveTo(s * 0.18, s * 0.28)
        path1.cubicTo(s * 0.42, s * 0.10, s * 0.58, s * 0.90, s * 0.82, s * 0.72)
        p.drawPath(path1)
        hx, hy = s * 0.82, s * 0.72
        head = QPainterPath()
        head.moveTo(hx, hy)
        head.lineTo(hx - s * 0.16, hy - s * 0.02)
        head.lineTo(hx - s * 0.04, hy - s * 0.16)
        head.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(c))
        p.drawPath(head)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        path2 = QPainterPath()
        path2.moveTo(s * 0.18, s * 0.72)
        path2.cubicTo(s * 0.42, s * 0.90, s * 0.58, s * 0.10, s * 0.82, s * 0.28)
        p.drawPath(path2)
        hx, hy = s * 0.82, s * 0.28
        head2 = QPainterPath()
        head2.moveTo(hx, hy)
        head2.lineTo(hx - s * 0.16, hy + s * 0.02)
        head2.lineTo(hx - s * 0.04, hy + s * 0.16)
        head2.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(c))
        p.drawPath(head2)
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



_SENTINEL = object()


def _is_full_prompt_key(key: str) -> bool:
    k = str(key or "")
    return k == "full" or k.startswith("full_")


def _full_col_bg(tint: str, empty: bool) -> str:
    """完整提示词双栏底：相对标题栏 #202020 再压暗，左蓝右绿。"""
    # 标题五字条背景作参照，先压暗再掺入蓝/绿边色
    darkened = _mix_hex("#202020", "#000000", 0.55)
    accent = "#2f8f55" if tint == "green" else "#3b82f6"
    return _mix_hex(darkened, accent, 0.08 if empty else 0.15)

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
        self.btn_lock = _LockMarkButton(self)
        self.btn_lock.setObjectName("PromptOrderLock")
        self.btn_lock.setCursor(Qt.PointingHandCursor)
        self.btn_lock.setFocusPolicy(Qt.NoFocus)
        self.btn_lock.setFixedSize(_TITLE_ACT, _TITLE_ACT)
        self.btn_lock.setToolTip("锁定")
        self.btn_lock.clicked.connect(
            lambda _=False, k=key: self._owner._toggle_lock_field(k)
        )
        self.btn_freeze = _FreezeMarkButton(self)
        self.btn_freeze.setObjectName("PromptOrderFreeze")
        self.btn_freeze.setCursor(Qt.PointingHandCursor)
        self.btn_freeze.setFocusPolicy(Qt.NoFocus)
        self.btn_freeze.setFixedSize(_TITLE_ACT, _TITLE_ACT)
        self.btn_freeze.setToolTip("")
        self.btn_freeze.clicked.connect(
            lambda _=False, k=key: self._owner._toggle_freeze_field(k)
        )
        self._place_card_actions()

    def _place_card_actions(self):
        lock = getattr(self, "btn_lock", None)
        freeze = getattr(self, "btn_freeze", None)
        gap = 4
        total = _TITLE_ACT * 2 + gap
        x0 = (self.width() - total) // 2
        y = self.height() - _TITLE_ACT - 4
        if lock is not None:
            lock.setGeometry(x0, y, _TITLE_ACT, _TITLE_ACT)
            lock.raise_()
        if freeze is not None:
            freeze.setGeometry(x0 + _TITLE_ACT + gap, y, _TITLE_ACT, _TITLE_ACT)
            freeze.raise_()

    def _place_freeze(self):
        self._place_card_actions()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_card_actions()

    def apply_title(self):
        self._lab.setFont(_order_card_font())
        self._lab.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        fm = QFontMetrics(_order_card_font())
        inner_w = _order_card_line_width()
        acts_vis = (
            (getattr(self, "btn_freeze", None) is not None and self.btn_freeze.isVisible())
            or (getattr(self, "btn_lock", None) is not None and self.btn_lock.isVisible())
        )
        extra = (_TITLE_ACT + 4) if acts_vis else 0
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
        if getattr(self, "btn_lock", None) is not None:
            self.btn_lock.hide()
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
            for attr, toggle in (
                ("btn_lock", "_toggle_lock_field"),
                ("btn_freeze", "_toggle_freeze_field"),
            ):
                btn = getattr(self, attr, None)
                if btn is not None and btn.isVisible() and btn.geometry().contains(e.pos()):
                    self._press = None
                    self._dragging = False
                    if self.childAt(e.pos()) is not btn:
                        getattr(self._owner, toggle)(self._key)
                    e.accept()
                    return
            self._press = e.globalPos()
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


class _EntryChipHost(QWidget):
    """词条弹窗顶部：流式排布，高度跟分类后面的词条区一样随宽度换行。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background:transparent;border:none;")
        self._flow = FlowLayout(self, 0, 6, 6)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width: int) -> int:
        return self._flow.heightForWidth(max(1, int(width)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refit()

    def refit(self):
        if getattr(self, "_fitting", False):
            return
        self._fitting = True
        try:
            h = self.heightForWidth(max(1, self.width()))
            if h > 0 and self.minimumHeight() != h:
                self.setMinimumHeight(h)
        finally:
            self._fitting = False


class _EntryManageDialog(QDialog):
    """一个分类里的词条：新建、修改、删除都在这个弹窗里。

    顶行是蓝色「+」和「新标题」。下面第一位是粘贴，接着是和分类后面一样的词条按钮。
    """

    _MARGIN = 14
    _INNER_PAD = 6
    _MIN_W = 588
    _MIN_H = 560

    def __init__(self, parent, sheet, key: str):
        super().__init__(parent)
        self._sheet = sheet
        self._key = str(key or "")
        self._loading = False
        self._rename_busy = False
        self._chips: List[QPushButton] = []
        title = ""
        for spec_key, spec_title, _ph in getattr(sheet, "_specs", []) or []:
            if spec_key == self._key:
                title = str(spec_title or "").strip()
                break
        self.setWindowTitle(f"词条 · {title}" if title else "词条")
        self.setModal(True)
        self.setMinimumSize(self._MIN_W, self._MIN_H)
        self.resize(self._MIN_W, self._MIN_H)
        self.setObjectName("PromptEntryDialog")
        self.setStyleSheet(
            "QDialog#PromptEntryDialog{background:%s;}"
            "QPlainTextEdit#PromptEntryValue{padding:%dpx;}"
            % (tk("panel"), self._INNER_PAD)
        )

        self._host = _EntryChipHost()
        self._flow = self._host._flow
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setWidget(self._host)
        self._scroll.setMinimumHeight(78)
        self._scroll.setMaximumHeight(210)
        self._scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollArea > QWidget > QWidget{background:transparent;}"
        )
        self._scroll.viewport().installEventFilter(self)

        self._btn_plus = _PlusButton()
        self._btn_plus.setObjectName("PromptEntryNewBtn")
        self._btn_plus.setFocusPolicy(Qt.NoFocus)
        self._btn_plus.setCursor(Qt.PointingHandCursor)
        self._btn_plus.setFixedSize(24, 24)
        self._btn_plus.setToolTip("新建")
        self._btn_plus.clicked.connect(self._on_new)

        self._new_name = QLineEdit()
        self._new_name.setObjectName("PromptEntryNewName")
        self._new_name.setPlaceholderText("新标题")
        self._new_name.setFixedHeight(24)
        self._new_name.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._new_name.setToolTip("新建用的标题。点已有词条不会把名字填到这里。")
        self._new_name.returnPressed.connect(self._on_new)
        self._style_top_bar()

        self._paste_btn = self._sheet._make_preset_paste_btn(self._key)
        self._flow.addWidget(self._paste_btn)
        paste_btns = getattr(self._sheet, "_preset_paste_btns", None)
        if paste_btns is not None:
            paste_btns.append(self._paste_btn)
        self.finished.connect(self._release_paste_btn)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        self._value = QPlainTextEdit()
        self._value.setObjectName("PromptEntryValue")
        self._value.setPlaceholderText("内容")
        self._value.setMinimumHeight(180)
        try:
            self._value.document().setDocumentMargin(0)
        except Exception:
            pass

        self._status = QLabel("点词条编辑内容。双击词条改标题。上面的「+」和「新标题」用来新建。")
        self._status.setWordWrap(True)
        self._status.setObjectName("PromptEntryStatus")
        self._apply_status(False)

        self._btn_save = QPushButton("保存并关闭")
        self._btn_del = QPushButton("删除")
        self._btn_close = QPushButton("关闭")
        for b in (self._btn_save, self._btn_del, self._btn_close):
            b.setAutoDefault(False)
            b.setDefault(False)
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumHeight(32)
        self._btn_save.clicked.connect(self._on_edit)
        self._btn_del.clicked.connect(self._on_delete)
        self._btn_close.clicked.connect(self.reject)
        self._style_action_btns()

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        actions.addWidget(self._btn_save)
        actions.addWidget(self._btn_close)
        actions.addStretch(1)
        actions.addWidget(self._btn_del)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        top.addWidget(self._btn_plus)
        top.addWidget(self._new_name, 1)

        lay = QVBoxLayout(self)
        m = self._MARGIN
        lay.setContentsMargins(m, m, m, m)
        lay.setSpacing(m)
        lay.addLayout(top)
        lay.addWidget(self._scroll, 0)
        lay.addWidget(self._value, 1)
        lay.addWidget(self._status, 0)
        lay.addLayout(actions)

        active = self._sheet._primary_active_value(self._key)
        self._reload(active)
        if active:
            self._value.setFocus()
        else:
            self._new_name.setFocus()

    def showEvent(self, event):
        super().showEvent(event)
        self._host.refit()

    def eventFilter(self, obj, event):
        if obj is self._scroll.viewport() and event.type() == QEvent.Resize:
            self._host.refit()
        elif event.type() == QEvent.MouseButtonDblClick and bool(obj.property("isEntryChip")):
            self._begin_rename(obj)
            return True
        elif obj is getattr(self, "_rename_edit", None) and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:
                finish = getattr(self, "_rename_finish", None)
                if callable(finish):
                    finish(True)
                return True
        return super().eventFilter(obj, event)

    def _release_paste_btn(self):
        btn = getattr(self, "_paste_btn", None)
        btns = getattr(self._sheet, "_preset_paste_btns", None)
        if btn is None or btns is None:
            return
        self._sheet._preset_paste_btns = [b for b in btns if b is not btn]

    def _style_top_bar(self):
        if theme.is_dark:
            b_bd, b_bg, b_fg = "#3b82f6", "#1e3a8a", "#eff6ff"
            h_bd, h_bg, h_fg = "#60a5fa", "#1d4ed8", "#ffffff"
            p_bd, p_bg, p_fg = "#2563eb", "#1e40af", "#dbeafe"
            i_bd, i_bg, i_fg, i_ph = "#3b82f6", "#1e3a8a", "#eff6ff", "#93c5fd"
        else:
            b_bd, b_bg, b_fg = "#2563eb", "#3b82f6", "#ffffff"
            h_bd, h_bg, h_fg = "#1d4ed8", "#2563eb", "#ffffff"
            p_bd, p_bg, p_fg = "#1e40af", "#1d4ed8", "#eff6ff"
            i_bd, i_bg, i_fg, i_ph = "#2563eb", "#dbeafe", "#1e3a8a", "#3b82f6"
        self._btn_plus.setStyleSheet(
            "QPushButton{"
            "min-width:24px;max-width:24px;min-height:24px;max-height:24px;"
            "padding:0;border-radius:6px;"
            f"border:1px solid {b_bd};background:{b_bg};color:{b_fg};"
            "font-size:16px;font-weight:700;}"
            "QPushButton:hover{"
            f"background:{h_bg};border-color:{h_bd};color:{h_fg};}}"
            "QPushButton:pressed{"
            f"background:{p_bg};border-color:{p_bd};color:{p_fg};}}"
        )
        self._btn_plus.set_plus_colors(b_fg, h_fg, p_fg, tk("text_dim"))
        self._new_name.setStyleSheet(
            "QLineEdit{"
            "min-height:24px;max-height:24px;padding:0 10px;border-radius:6px;"
            f"border:1px solid {i_bd};background:{i_bg};color:{i_fg};"
            "font-size:12px;font-weight:600;}"
        )
        pal = self._new_name.palette()
        pal.setColor(QPalette.PlaceholderText, QColor(i_ph))
        self._new_name.setPalette(pal)

    def _style_action_btns(self):
        if theme.is_dark:
            bd, bg, fg = "#64748b", "#1e293b", tk("text")
            del_bd, del_bg, del_fg = "#7f1d1d", "#450a0a", "#fecaca"
            s_bd, s_bg, s_fg = "#16a34a", "#14532d", "#dcfce7"
            s_h_bd, s_h_bg, s_h_fg = "#86efac", "#166534", "#f0fdf4"
        else:
            bd, bg, fg = "#94a3b8", "#f8fafc", tk("text")
            del_bd, del_bg, del_fg = "#fca5a5", "#fef2f2", "#b91c1c"
            s_bd, s_bg, s_fg = "#15803d", "#dcfce7", "#14532d"
            s_h_bd, s_h_bg, s_h_fg = "#15803d", "#bbf7d0", "#14532d"
        base = (
            "QPushButton{padding:0 14px;border-radius:6px;"
            "border:1px solid %s;background:%s;color:%s;font-weight:600;}"
            "QPushButton:hover{background:%s;border-color:%s;color:%s;}"
            "QPushButton:disabled{color:%s;}"
        )
        self._btn_close.setStyleSheet(base % (bd, bg, fg, bg, bd, fg, tk("text_dim")))
        self._btn_del.setStyleSheet(
            base % (del_bd, del_bg, del_fg, del_bg, del_bd, del_fg, tk("text_dim"))
        )
        self._btn_save.setStyleSheet(
            base % (s_bd, s_bg, s_fg, s_h_bg, s_h_bd, s_h_fg, tk("text_dim"))
        )

    def _apply_status(self, bad: bool, text: str = ""):
        if text:
            self._status.setText(text)
        if bad:
            color = "#fca5a5" if theme.is_dark else "#b91c1c"
        else:
            color = "#86efac" if theme.is_dark else "#15803d"
        if not text and not bad:
            color = tk("text_dim")
        self._status.setStyleSheet(
            f"QLabel#PromptEntryStatus{{color:{color};background:transparent;border:none;}}"
        )

    def _selected_chip(self):
        btn = self._group.checkedButton()
        if btn is None:
            return None
        try:
            btn.objectName()
        except RuntimeError:
            return None
        return btn

    def _chip_identity(self, btn):
        if btn is None:
            return "", ""
        return (
            str(btn.property("chipName") or "").strip(),
            str(btn.property("chipValue") or "").strip(),
        )

    def _sync_buttons(self):
        has = self._selected_chip() is not None
        self._btn_save.setEnabled(has)
        self._btn_del.setEnabled(has)

    def _clear_chips(self):
        self._group.setExclusive(False)
        i = 0
        while i < self._flow.count():
            item = self._flow.itemAt(i)
            w = item.widget() if item is not None else None
            if w is None or w is self._paste_btn:
                i += 1
                continue
            self._flow.takeAt(i)
            try:
                self._group.removeButton(w)
            except RuntimeError:
                pass
            w.setParent(None)
            w.deleteLater()
        self._chips = []
        self._group.setExclusive(True)

    def _reload(self, select_value: str = ""):
        self._loading = True
        self._clear_chips()
        want = (select_value or "").strip()
        picked = None
        for src, name, value in self._sheet._ordered_entries(self._key):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(24)
            btn.setProperty("chipName", name)
            btn.setProperty("chipValue", value)
            btn.setProperty("chipCustom", src == "custom")
            btn.setToolTip(value)
            try:
                self._sheet._style_chip(btn)
            except Exception:
                pass
            btn.clicked.connect(lambda _=False, b=btn: self._on_chip(b))
            btn.setProperty("isEntryChip", True)
            btn.installEventFilter(self)
            self._group.addButton(btn)
            self._flow.addWidget(btn)
            self._chips.append(btn)
            if want and value == want and picked is None:
                picked = btn
        self._loading = False
        if picked is not None:
            picked.setChecked(True)
            self._show_chip(picked)
        else:
            self._value.clear()
            self._sync_buttons()
        self._host.refit()

    def _show_chip(self, btn):
        _name, value = self._chip_identity(btn)
        self._value.setPlainText(value)
        self._sync_buttons()

    def _on_chip(self, btn):
        if self._loading:
            return
        self._show_chip(btn)

    def _begin_rename(self, btn):
        if self._rename_busy or btn is None:
            return
        old_name, old_value = self._chip_identity(btn)
        if not old_value:
            return
        edit = QLineEdit(btn)
        edit.setText(old_name)
        edit.setGeometry(1, 1, max(8, btn.width() - 2), max(8, btn.height() - 2))
        edit.setStyleSheet(
            "QLineEdit{border:none;background:transparent;padding:0 8px;"
            "font-size:12px;font-weight:600;}"
        )
        edit.selectAll()
        edit.show()
        edit.setFocus()

        def _finish(cancel: bool = False):
            if self._rename_busy:
                return
            self._rename_busy = True
            try:
                new_name = "" if cancel else (edit.text() or "").strip()
                edit.hide()
                edit.deleteLater()
            finally:
                self._rename_busy = False
            if cancel or not new_name or new_name == old_name:
                return
            ok, msg = self._sheet._update_prompt_entry(
                self._key, old_name, old_value, new_name, old_value,
            )
            self._apply_status(not ok, msg)
            if ok:
                self._reload(old_value)

        edit.editingFinished.connect(lambda: _finish(False))
        edit.installEventFilter(self)
        edit.setProperty("renameCancel", False)
        self._rename_edit = edit
        self._rename_finish = _finish

    def _on_new(self):
        ok, msg = self._sheet._create_prompt_entry(
            self._key, self._new_name.text(), self._value.toPlainText(),
        )
        if not ok:
            self._apply_status(True, msg)
            return
        _n, value, _err = _normalize_entry_text(
            self._new_name.text(), self._value.toPlainText(),
        )
        self._new_name.clear()
        self._reload(value)
        self._apply_status(False, msg)

    def _on_edit(self):
        btn = self._selected_chip()
        old_name, old_value = self._chip_identity(btn)
        if not old_value:
            self._apply_status(True, "先点一条词条")
            return
        ok, msg = self._sheet._update_prompt_entry(
            self._key, old_name, old_value, old_name, self._value.toPlainText(),
        )
        if not ok:
            self._apply_status(True, msg)
            return
        self.accept()

    def _on_delete(self):
        btn = self._selected_chip()
        name, value = self._chip_identity(btn)
        if not value:
            self._apply_status(True, "先点一条词条")
            return
        if not _confirm_delete_entry(self, name):
            return
        values = [str(b.property("chipValue") or "") for b in self._chips]
        try:
            idx = values.index(value)
        except ValueError:
            idx = -1
        nxt = ""
        if idx >= 0:
            if idx + 1 < len(values):
                nxt = values[idx + 1]
            elif idx > 0:
                nxt = values[idx - 1]
        ok, msg = self._sheet._delete_prompt_entry(self._key, name, value)
        if not ok:
            self._apply_status(True, msg)
            return
        self._reload(nxt)
        self._apply_status(False, msg)


class _FreezeVeil(QWidget):
    """盖在编辑框+预设上的黑罩：冻结时整区不可点。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PromptFreezeVeil")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.ForbiddenCursor)
        self.setVisible(False)
        self._caption = "已冻结"

    def set_caption(self, text: str):
        text = (text or "").strip() or "已冻结"
        if text == self._caption:
            return
        self._caption = text
        self.update()

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
        fm = p.fontMetrics()
        # 冻结行只剩这一行：字跟在色条后面，本行左对齐，垂直与图标同高
        pad_l, pad_r = 8, 8
        area = self.rect().adjusted(pad_l, 0, -pad_r, 0)
        shown = fm.elidedText(self._caption, Qt.ElideRight, max(0, area.width()))
        p.drawText(area, Qt.AlignLeft | Qt.AlignVCenter, shown)


class _FreezeMarkButton(QPushButton):
    """标题色条上的冻结钮。眼睛自己画在控件正中，不用 QIcon。

    QPushButton 的图标会偏右、偏下，冻成一行时色条又窄，差一像素就看得出。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frozen = False
        self._ink = QColor("#94a3b8")

    def set_mark(self, frozen: bool, color: str):
        self._frozen = bool(frozen)
        self._ink = QColor(color)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            s = float(_PASTE_ICON)
            w, h = s * 0.80, s * 0.46
            path = QPainterPath()
            if self._frozen:
                path.moveTo(-w / 2.0, 0.0)
                path.quadTo(0.0, h * 0.62, w / 2.0, 0.0)
            else:
                path.moveTo(-w / 2.0, 0.0)
                path.cubicTo(-w / 2.0, -h / 2.0, w / 2.0, -h / 2.0, w / 2.0, 0.0)
                path.cubicTo(w / 2.0, h / 2.0, -w / 2.0, h / 2.0, -w / 2.0, 0.0)
            br = path.boundingRect()
            path.translate(-br.center().x(), -br.center().y())
            p.translate(self.width() / 2.0, self.height() / 2.0)
            pen = QPen(self._ink, max(1.2, s * 0.10))
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
            if not self._frozen:
                pr = max(1.0, s * 0.085)
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(self._ink))
                p.drawEllipse(QPointF(0.0, 0.0), pr, pr)
        finally:
            p.end()



class _LockVeil(QWidget):
    """Semi-transparent overlay over a full category section when locked."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PromptLockVeil")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.ForbiddenCursor)
        self.setVisible(False)
        self._caption = "已锁定"

    def set_caption(self, text: str):
        text = (text or "").strip() or "已锁定"
        if text == self._caption:
            return
        self._caption = text
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 120))
        p.drawRoundedRect(QRectF(self.rect()), 4, 4)
        p.setPen(QColor("#fde68a"))
        f = QFont()
        f.setPixelSize(13)
        f.setBold(True)
        p.setFont(f)
        fm = p.fontMetrics()
        pad_l, pad_r = 8, 8
        area = self.rect().adjusted(pad_l, 0, -pad_r, 0)
        shown = fm.elidedText(self._caption, Qt.ElideRight, max(0, area.width()))
        p.drawText(area, Qt.AlignHCenter | Qt.AlignVCenter, shown)


class _LockMarkButton(QPushButton):
    """Padlock mark drawn in-control (like _FreezeMarkButton)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._locked = False
        self._ink = QColor("#94a3b8")

    def set_mark(self, locked: bool, color: str):
        self._locked = bool(locked)
        self._ink = QColor(color)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            s = float(_PASTE_ICON)
            bw, bh = s * 0.62, s * 0.48
            body = QRectF(-bw / 2.0, s * 0.02, bw, bh)
            sh_w, sh_h = s * 0.42, s * 0.36
            sh = QRectF(-sh_w / 2.0, -s * 0.36, sh_w, sh_h)
            pen = QPen(self._ink, max(1.2, s * 0.11))
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.translate(self.width() / 2.0, self.height() / 2.0)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            if self._locked:
                p.drawArc(sh, 0 * 16, 180 * 16)
            else:
                p.drawArc(sh, 40 * 16, 160 * 16)
            if self._locked:
                p.setBrush(QBrush(self._ink))
            else:
                p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(body, 1.5, 1.5)
            p.setPen(Qt.NoPen)
            if self._locked:
                p.setBrush(QBrush(QColor("#0f172a")))
            else:
                p.setBrush(QBrush(self._ink))
            pr = max(0.8, s * 0.06)
            p.drawEllipse(QPointF(0.0, s * 0.12), pr, pr)
        finally:
            p.end()


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

    def set_glyph_colors(self, normal: str, hover: str, press: str, disabled: str):
        self.set_plus_colors(normal, hover, press, disabled)


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
        # 完整提示词译缓存：(lang, key, src_text) -> out_text；worker 句柄
        self._tr_cache: Dict[Tuple[str, str, str], str] = {}
        self._tr_worker = None
        self._tr_req_id = 0
        self._tr_status = ""  # translating | error | ready | ""
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
        self._locked_keys = set()
        self._batch_picking = False
        self._freeze_btns: Dict[str, QPushButton] = {}
        self._freeze_veils: Dict[str, QWidget] = {}
        self._lock_veils: Dict[str, QWidget] = {}
        self._preset_panes: Dict[str, QFrame] = {}
        self._preset_flows: Dict[str, FlowLayout] = {}
        self._chip_groups: Dict[str, QButtonGroup] = {}
        self._chip_btns: List[QPushButton] = []
        self._add_btns: List[QPushButton] = []
        self._preset_paste_btns: List[QPushButton] = []
        self._custom_presets: Dict[str, List[dict]] = {}
        # H3 预设：展开状态由容器共享（落 prompt_editor.h3_expanded）；按钮首次展开才建
        self._h3_expanded: Dict[str, bool] = {}
        self._h3_toggling = False
        self._h3_collapse_pending = None
        self._h3_scroll_pending = None  # key to center after H3 height settles
        self._scroll_center_pending = None  # clicked chip/widget to center
        self._scroll_center_hint = None  # (key, value) fallback if widget rebuilt
        self._scroll_center_gen = 0  # bump each request; deferred recenter checks it
        self._debug_scroll_center = False  # set True to log chip-center path
        self._scroll_center_hold = False  # keep hint until final deferred clear
        self._row_sync_pending = set()
        self._entry_btns: Dict[str, Dict[str, QPushButton]] = {}
        self._field_fixed: Dict[str, tuple] = {}
        self._entry_sig: Dict[str, tuple] = {}
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
        # 完整提示词富文本重建较重：输入时防抖，避免每键卡顿
        self._full_prompt_timer = QTimer(self)
        self._full_prompt_timer.setSingleShot(True)
        self._full_prompt_timer.setInterval(160)
        self._full_prompt_timer.timeout.connect(self._refresh_full_prompt)

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
        # 底边距：完整提示词末行余量（复制已迁至底栏）
        root.setContentsMargins(0, 0, 0, 12)
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
        self.btn_field_rename = self._make_order_action_btn(
            "改", "给选中的分类卡改名"
        )
        self.btn_field_del = self._make_order_action_btn(
            "-", "删除选中的分区"
        )
        self.btn_field_add.clicked.connect(self._add_field_clicked)
        self.btn_field_rename.clicked.connect(self._rename_selected_field)
        self.btn_field_del.clicked.connect(self._delete_selected_field)
        self.btn_pick_random = QPushButton()
        self.btn_pick_random.setProperty("pickKind", "random")
        self.btn_pick_random.setToolTip("随机")
        self.btn_pick_random.setCursor(Qt.PointingHandCursor)
        self.btn_pick_random.setFocusPolicy(Qt.NoFocus)
        self.btn_pick_random.setIconSize(QSize(_HIST_ICON, _HIST_ICON))
        self.btn_pick_random.clicked.connect(self._pick_entries_random)
        self.btn_pick_seq = QPushButton()
        self.btn_pick_seq.setProperty("pickKind", "seq")
        self.btn_pick_seq.setToolTip("顺序")
        self.btn_pick_seq.setCursor(Qt.PointingHandCursor)
        self.btn_pick_seq.setFocusPolicy(Qt.NoFocus)
        self.btn_pick_seq.setIconSize(QSize(_HIST_ICON, _HIST_ICON))
        self.btn_pick_seq.clicked.connect(self._pick_entries_sequential)
        for btn in (
            self.btn_field_add,
            self.btn_field_rename,
            self.btn_field_del,
            self.btn_pick_random,
            self.btn_pick_seq,
        ):
            btn.setMinimumWidth(0)
            btn.setFixedSize(_COPY_H, _COPY_H)
            btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self._style_field_square_btn(btn)
        self._style_pick_btn(self.btn_pick_random)
        self._style_pick_btn(self.btn_pick_seq)
        left_lay.addWidget(self.btn_field_add, 0)
        left_lay.addWidget(self.btn_field_rename, 0)
        left_lay.addWidget(self.btn_field_del, 0)
        left_lay.addWidget(self.btn_pick_random, 0)
        left_lay.addWidget(self.btn_pick_seq, 0)
        left_lay.addWidget(self._make_translate_model_combo(), 0)
        left_lay.addStretch(1)
        right_foot = QWidget()
        right_foot.setAttribute(Qt.WA_StyledBackground, True)
        right_foot.setStyleSheet("background:transparent;border:none;")
        right_lay = QHBoxLayout(right_foot)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(6)
        # 右侧：跳转完整提示词 + 历史
        right_foot.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        right_lay.addWidget(self._make_full_action_btn("jump"), 0)
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
        left_foot.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        foot.addWidget(left_foot, 0)
        foot.addStretch(1)
        foot.addWidget(right_foot, 0)
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
        lock_veil = _LockVeil(wrap)
        self._lock_veils[key] = lock_veil
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
        # 「+」和前 15 条留在上面的流里，两块按序号重新分配
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
        # 自定义预设已并入 _ordered_entries（参与 MRU / 前 15 / H3），这里不再单独建
        customs: List[QPushButton] = []
        add_btn = self._make_add_preset_btn(key)
        flow.addWidget(add_btn)
        self._add_btns.append(add_btn)
        self._field_fixed[key] = (customs, add_btn)
        self._entry_sig.pop(key, None)
        self._layout_field_entries(key)
        self._sync_field_chips(key)
        self._refresh_preset_paste_btns()

    def _txt_pairs(self, key: str) -> List[Tuple[str, str, str]]:
        """单层 txt 预设；读不到再用代码里的 _CHAR_PRESETS。"""
        data = _txt_presets()
        if data is not None:
            return [("txt", n, v) for n, v in (data.get(key) or [])]
        return self._char_preset_pairs(_CHAR_PRESET_KEYS.get(key) or ())

    # 兼容旧调用名（曾分内置 / H3 两层）
    def _builtin_pairs(self, key: str) -> List[Tuple[str, str, str]]:
        return self._txt_pairs(key)

    @staticmethod
    def _h3_entries(key: str) -> List[Tuple[str, str]]:
        """已废弃：H3 仅为 UI 溢出，不再是独立数据层。"""
        return []

    def _field_chip_values(self, key: str) -> List[str]:
        """这一栏所有预设内容（自定义 + txt，含未展开的溢出条），用于长条优先判定。"""
        vals = [v for _s, _n, v in self._txt_pairs(key)]
        for it in self._custom_presets.get(key) or []:
            v = str((it or {}).get("value") or "").strip()
            if v:
                vals.append(v)
        return vals

    def _active_values_for(self, key: str) -> set:
        ed = self._left.get(key)
        text = (ed.toPlainText() if ed is not None else "") or ""
        if not text.strip():
            return set()
        return _active_chip_values(text, self._field_chip_values(key))

    def _ordered_entries(self, key: str) -> List[Tuple[str, str, str]]:
        """(来源, 标题, 内容)；自定义在前、txt 在后、按内容去重。

        txt 顺序即 docs/提示词分类.txt 该分类内序号（点 chip 写入时物理提到 1.）。
        UI：前 _TOP_N 条进顶栏 chip，其余进 H3 溢出区（仅当 len>_TOP_N 时显示蓝色 H3）。
        """
        entries: List[Tuple[str, str, str]] = []
        seen = set()
        # 默认顺序：本栏自定义预设 → txt 单层列表（同内容只留第一个，即自定义优先）
        for it in self._custom_presets.get(key) or []:
            name = str((it or {}).get("name") or "").strip()
            value = str((it or {}).get("value") or "").strip()
            if name and value and value not in seen:
                seen.add(value)
                entries.append(("custom", name, value))
        for src, name, value in self._txt_pairs(key):
            if value and value not in seen:
                seen.add(value)
                entries.append((src, name, value))
        return entries

    def _h3_is_expanded(self, key: str) -> bool:
        return bool((getattr(self, "_h3_expanded", None) or {}).get(key))


    def _collapse_h3_except(self, keep_key: str = None, *, quiet: bool = False):
        """收起 keep_key 以外所有已展开的 H3；keep_key 为 None 则全部收起。

        quiet=True：只改展开状态并重排流，不逐栏 after_layout / 不通知落盘
        （由调用方批量同步高度，避免连续抖动）。返回被收起的 key 列表。
        """
        expanded = getattr(self, "_h3_expanded", None) or {}
        to_close = [k for k, v in list(expanded.items()) if v and k != keep_key]
        if not to_close:
            return []
        for k in to_close:
            self._h3_expanded.pop(k, None)
            built = self._layout_field_entries(k, force=True)
            if quiet:
                if built:
                    self._sync_field_chips(k)
            else:
                self._after_entry_layout(k, built)
        if not quiet:
            self._notify_prefs_only(" H3 展开状态")
        return to_close

    def _note_category_activity(self, key: str):
        if getattr(self, "_batch_picking", False):
            return
        """用户在某分类操作：若其它分类 H3 展开则自动收起。完整提示词区视为分类外。"""
        if self._loading or getattr(self, "_hist_applying", False):
            return
        if getattr(self, "_h3_toggling", False):
            return
        key = str(key or "")
        if _is_full_prompt_key(key):
            keep = None
        elif not key:
            return
        else:
            expanded = [k for k, v in (getattr(self, "_h3_expanded", {}) or {}).items() if v]
            if not expanded:
                return
            if all(k == key for k in expanded):
                return
            keep = key
        # 合并同事件循环内多次 textChanged / 芯片操作触发的自动收起
        pending = getattr(self, "_h3_collapse_pending", None)
        if pending is not None and pending == keep:
            return
        self._h3_collapse_pending = keep
        QTimer.singleShot(0, self._flush_pending_h3_collapse)

    def _flush_pending_h3_collapse(self):
        keep = getattr(self, "_h3_collapse_pending", _SENTINEL)
        self._h3_collapse_pending = None
        if keep is _SENTINEL:
            return
        if getattr(self, "_h3_toggling", False) or self._loading:
            return
        # Chip target must survive this flush (do NOT clear here).
        has_chip = (
            getattr(self, "_scroll_center_pending", None) is not None
            or bool(getattr(self, "_scroll_center_hint", None))
        )
        scroll = getattr(self, "_scroll", None)
        bar = None
        saved_val = None
        # Only save/restore when no chip to center (full-prompt activity).
        if keep is None and not has_chip and scroll is not None:
            try:
                bar = scroll.verticalScrollBar()
                if bar is not None:
                    saved_val = int(bar.value())
            except Exception:
                bar = None
                saved_val = None

        freeze: List[QWidget] = []
        try:
            if scroll is not None:
                try:
                    scroll.setUpdatesEnabled(False)
                    freeze.append(scroll)
                    host = scroll.widget()
                    if host is not None:
                        host.setUpdatesEnabled(False)
                        freeze.append(host)
                except RuntimeError:
                    pass

            closed = self._collapse_h3_except(keep_key=keep, quiet=True)
            if closed:
                for k in closed:
                    self._sync_section_row(k)
                self._notify_prefs_only(" H3 展开状态")
            if keep:
                try:
                    self._sync_section_row(str(keep))
                except Exception:
                    pass

            # After height is stable: always center clicked chip if any.
            # Prefer chip over keep H3 header; prefer chip over save/restore.
            if has_chip:
                self._scroll_center_hold = True
                self._apply_pending_scroll_center(clear=False)
            elif keep:
                self._h3_scroll_pending = str(keep)
                self._scroll_after_h3_change(str(keep))
            elif bar is not None and saved_val is not None:
                try:
                    bar.setValue(min(int(saved_val), int(bar.maximum())))
                except Exception:
                    pass
        finally:
            for w in freeze:
                try:
                    w.setUpdatesEnabled(True)
                except RuntimeError:
                    pass

        # Delayed heightForWidth / full-prompt (160ms) may reflow — recenter.
        if has_chip or keep:
            gen = int(getattr(self, "_scroll_center_gen", 0) or 0)
            QTimer.singleShot(0, lambda g=gen: self._deferred_scroll_center(g, False))
            if has_chip:
                # After full-prompt debounce (160ms), final center then clear.
                QTimer.singleShot(200, lambda g=gen: self._deferred_scroll_center(g, True))
            elif keep:
                QTimer.singleShot(0, lambda g=gen: self._deferred_scroll_center(g, True))
            if keep:
                self._queue_row_sync(str(keep))


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
        """按当前顺序摆放：「+」、前 _TOP_N 条、（若有溢出）蓝色「H3 ▸/▾」。

        「+」在第一条词条前面，打开词条弹窗。粘贴在弹窗里，排在词条前面。
        前 _TOP_N 条总是建好并显示；其余只有展开（且页面可见或 force）时才建；
        len(entries)<=_TOP_N 时不创建/不显示 H3。返回是否新建了按钮。
        """
        flow = self._preset_flows.get(key)
        group = self._chip_groups.get(key)
        fixed = self._field_fixed.get(key)
        if flow is None or group is None or fixed is None:
            return False
        customs, add_btn = fixed
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
        order: List[QWidget] = [add_btn]
        order += [btns[e[2]] for e in top]
        order += list(customs)
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
        # 大批量建钮时冻结绘制，避免 hide/show 顶栏流造成闪烁
        pane = self._preset_panes.get(key)
        freeze_paint = pane is not None and (len(new_btns) > 3 or getattr(self, "_h3_toggling", False))
        if freeze_paint:
            pane.setUpdatesEnabled(False)
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
            for b, vis in want:
                if b.isHidden() == vis:
                    b.setHidden(not vis)
            if h3_host is not None:
                show_h3 = expanded and bool(rest_btns)
                if h3_host.isHidden() == show_h3:
                    h3_host.setVisible(show_h3)
        finally:
            if freeze_paint:
                pane.setUpdatesEnabled(True)
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
        # H3 expand/collapse: app blue #3b82f6, distinct from preset chips
        if theme.is_dark:
            bd, bg, fg = "#3b82f6", "#1e3a8a", "#eff6ff"
            h_bd, h_bg, h_fg = "#60a5fa", "#1d4ed8", "#ffffff"
            p_bd, p_bg, p_fg = "#2563eb", "#1e40af", "#dbeafe"
        else:
            bd, bg, fg = "#2563eb", "#3b82f6", "#ffffff"
            h_bd, h_bg, h_fg = "#1d4ed8", "#2563eb", "#ffffff"
            p_bd, p_bg, p_fg = "#1e40af", "#1d4ed8", "#eff6ff"
        btn.setCheckable(False)
        btn.setStyleSheet(
            "QPushButton{"
            f"min-height:24px;max-height:24px;padding:0 10px;border-radius:6px;"
            f"border:1px solid {bd};background:{bg};color:{fg};"
            f"font-size:12px;font-weight:700;}}"
            "QPushButton:hover{"
            f"background:{h_bg};border-color:{h_bd};color:{h_fg};}}"
            "QPushButton:pressed{"
            f"background:{p_bg};border-color:{p_bd};color:{p_fg};}}"
            "QPushButton:disabled{"
            f"background:{tk('accent_dis')};border-color:{tk('border')};"
            f"color:{tk('text_dim')};}}"
        )

    def _notify_prefs_only(self, what: str):
        """只通知落盘（展开状态等），不进撤销历史。"""
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
        # 立即量一次；同事件循环内多次布局合并到一次延迟重测，避免连跳两帧高度
        self._sync_section_row(key)
        self._queue_row_sync(key)

    def _queue_row_sync(self, key: str):
        pending = getattr(self, "_row_sync_pending", None)
        if pending is None:
            self._row_sync_pending = set()
            pending = self._row_sync_pending
        if key in pending:
            return
        pending.add(key)
        if len(pending) == 1:
            QTimer.singleShot(0, self._flush_pending_row_syncs)

    def _center_widget_in_scroll(self, widget: QWidget):
        """Vertically center widget in PromptScroll viewport (manual bar setValue)."""
        scroll = getattr(self, "_scroll", None)
        if scroll is None or widget is None:
            return
        try:
            host = scroll.widget()
            if host is None:
                return
            bar = scroll.verticalScrollBar()
            if bar is None:
                return
            mid = widget.mapTo(host, QPoint(0, max(0, int(widget.height()) // 2)))
            vh = max(1, int(scroll.viewport().height()))
            target = int(mid.y()) - vh // 2
            lo, hi = 0, int(bar.maximum())
            value = max(lo, min(hi, target))
            bar.setValue(value)
            if getattr(self, "_debug_scroll_center", False):
                try:
                    log.info(
                        "scroll_center y=%s vh=%s target=%s value=%s max=%s widget=%s",
                        mid.y(), vh, target, value, hi, type(widget).__name__,
                    )
                except Exception:
                    pass
        except Exception:
            if getattr(self, "_debug_scroll_center", False):
                log.exception("scroll_center failed")

    def _scroll_after_h3_change(self, key: str):
        """After H3 expand/collapse, center the toggled category chip / section."""
        key = str(key or "")
        if not key:
            return
        w = (getattr(self, "_h3_toggles", None) or {}).get(key)
        if w is None:
            w = (getattr(self, "_sections", None) or {}).get(key)
        else:
            try:
                if w.isHidden():
                    w = (getattr(self, "_sections", None) or {}).get(key) or w
            except RuntimeError:
                w = (getattr(self, "_sections", None) or {}).get(key)
        if w is None:
            return
        self._center_widget_in_scroll(w)

    def _peek_scroll_center_widget(self):
        """Resolve clicked chip without clearing pending (for multi-pass center)."""
        w = getattr(self, "_scroll_center_pending", None)
        if w is not None:
            try:
                w.objectName()
                return w
            except RuntimeError:
                w = None
                self._scroll_center_pending = None
        hint = getattr(self, "_scroll_center_hint", None)
        if hint:
            hk, hv = hint
            btns = (getattr(self, "_entry_btns", None) or {}).get(hk) or {}
            w = btns.get(hv)
            if w is not None:
                try:
                    w.objectName()
                    self._scroll_center_pending = w  # refresh live ref
                    return w
                except RuntimeError:
                    return None
        return None

    def _clear_scroll_center_pending(self):
        self._scroll_center_pending = None
        self._scroll_center_hint = None
        self._scroll_center_hold = False

    def _apply_pending_scroll_center(self, clear: bool = False) -> bool:
        w = self._peek_scroll_center_widget()
        if w is None:
            if clear:
                self._clear_scroll_center_pending()
            return False
        self._center_widget_in_scroll(w)
        if clear:
            self._clear_scroll_center_pending()
        return True

    def _deferred_scroll_center(self, gen: int = None, clear: bool = True):
        """Later-pass center after deferred layout / heightForWidth / full-prompt."""
        if gen is not None and int(getattr(self, "_scroll_center_gen", 0) or 0) != int(gen):
            return
        # Sync any queued row heights first so mapTo uses final geometry.
        pending = getattr(self, "_row_sync_pending", None) or set()
        if pending and not getattr(self, "_h3_toggling", False):
            self._row_sync_pending = set()
            for k in list(pending):
                try:
                    self._sync_section_row(k)
                except Exception:
                    pass
        if self._apply_pending_scroll_center(clear=clear):
            return
        if clear:
            scroll_key = getattr(self, "_h3_scroll_pending", None)
            self._h3_scroll_pending = None
            if scroll_key:
                self._scroll_after_h3_change(str(scroll_key))

    def _arm_scroll_center(self, widget: QWidget = None, key: str = None, value: str = None):
        """Remember clicked chip; do not schedule timers (caller decides)."""
        key = str(key or "")
        value = str(value or "").strip()
        alive = None
        if widget is not None:
            try:
                widget.objectName()
                alive = widget
            except RuntimeError:
                alive = None
        self._scroll_center_pending = alive
        self._scroll_center_hint = (key, value) if (key and value) else None
        self._scroll_center_gen = int(getattr(self, "_scroll_center_gen", 0) or 0) + 1
        if getattr(self, "_debug_scroll_center", False):
            try:
                log.info(
                    "scroll_center_arm key=%s value=%r gen=%s",
                    key, (value[:40] + "...") if len(value) > 40 else value,
                    self._scroll_center_gen,
                )
            except Exception:
                pass

    def _schedule_scroll_center(self, key: str = None):
        """Schedule deferred center when no H3 auto-collapse is pending."""
        key = str(key or "")
        collapse_keep = getattr(self, "_h3_collapse_pending", None)
        if isinstance(collapse_keep, str) and collapse_keep:
            return
        if key:
            self._queue_row_sync(key)
        else:
            gen = int(getattr(self, "_scroll_center_gen", 0) or 0)
            QTimer.singleShot(0, lambda g=gen: self._deferred_scroll_center(g))

    def _request_scroll_center(self, widget: QWidget = None, key: str = None, value: str = None):
        """Arm clicked entry and schedule center unless collapse flush will own it."""
        self._arm_scroll_center(widget, key, value)
        self._schedule_scroll_center(key)


    def _resolve_scroll_center_widget(self):
        """Resolve and clear pending chip (single-shot consumers)."""
        w = self._peek_scroll_center_widget()
        self._clear_scroll_center_pending()
        return w

    def _flush_pending_row_syncs(self):
        pending = getattr(self, "_row_sync_pending", None) or set()
        self._row_sync_pending = set()
        if getattr(self, "_h3_toggling", False):
            for k in pending:
                self._queue_row_sync(k)
            return
        for k in list(pending):
            self._sync_section_row(k)
        # Prefer chip center; collapse path may still hold hint for delayed pass.
        collapse_keep = getattr(self, "_h3_collapse_pending", None)
        collapse_queued = isinstance(collapse_keep, str) and bool(collapse_keep)
        if collapse_queued:
            # Collapse flush owns final center; just sync heights here.
            return
        # Keep hint alive until final deferred clear after collapse reflow.
        hold = bool(getattr(self, "_scroll_center_hold", False))
        if self._apply_pending_scroll_center(clear=not hold):
            return
        scroll_key = getattr(self, "_h3_scroll_pending", None)
        self._h3_scroll_pending = None
        if scroll_key:
            self._scroll_after_h3_change(str(scroll_key))


    def _toggle_h3(self, key: str):
        if len(self._ordered_entries(key)) <= _TOP_N:
            return
        if getattr(self, "_h3_toggling", False):
            return
        expanding = not self._h3_is_expanded(key)
        # 先算将被连带收起的栏，再统一冻绘制
        will_close = []
        if expanding:
            exp = getattr(self, "_h3_expanded", None) or {}
            will_close = [k for k, v in list(exp.items()) if v and k != key]
        self._h3_toggling = True
        freeze: List[QWidget] = []
        try:
            for k in [key] + will_close:
                w = self._sections.get(k)
                if w is not None:
                    w.setUpdatesEnabled(False)
                    freeze.append(w)
            closed: List[str] = []
            if expanding:
                closed = list(self._collapse_h3_except(keep_key=key, quiet=True) or [])
                self._h3_expanded[key] = True
            else:
                self._h3_expanded.pop(key, None)
            built = self._layout_field_entries(key, force=True)
            if built:
                self._sync_field_chips(key)
            # 一次性同步高度：不碰完整提示词（H3 与正文无关）
            for k in [key] + closed:
                self._sync_section_row(k)
            self._notify_prefs_only(" H3 展开状态")
        finally:
            for w in freeze:
                try:
                    w.setUpdatesEnabled(True)
                except RuntimeError:
                    pass
            self._h3_toggling = False
            # height settle then center toggled H3 / section
            self._h3_scroll_pending = key
            self._queue_row_sync(key)

    def _promote_preset_chip(self, btn: QPushButton):
        """点 chip 写入后：把该条提到本栏最前。

        - txt 条目：重写 docs/提示词分类.txt 对应【分类】段（点中的变 1.，其余顺延），刷新缓存与 chip 顺序。
        - 自定义：只把该项挪到本栏自定义列表最前并落盘（不写进分类 txt）。
        """
        key = str(btn.property("promptKey") or "")
        value = str(btn.property("chipValue") or "").strip()
        name = str(btn.property("chipName") or "").strip()
        if not key or not value:
            return
        if bool(btn.property("chipCustom")):
            items = list(self._custom_presets.get(key) or [])
            hit = None
            for i, it in enumerate(items):
                v = str((it or {}).get("value") or "").strip()
                n = str((it or {}).get("name") or "").strip()
                if v == value and (not name or n == name):
                    hit = i
                    break
            if hit is None:
                for i, it in enumerate(items):
                    if str((it or {}).get("value") or "").strip() == value:
                        hit = i
                        break
            if hit is None or hit == 0:
                return
            item = items.pop(hit)
            items.insert(0, item)
            self._custom_presets[key] = items
            self._publish_presets()
        else:
            if not _promote_txt_preset_entry(key, value, name):
                return
        built = self._layout_field_entries(key, force=True)
        self._after_entry_layout(key, built)

    def _sync_h3_all(self):
        """进入页面时按共享的展开状态 / 文件顺序补建、重排（别的分页可能改过）。"""
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
        return btn

    def _make_add_preset_btn(self, key: str) -> QPushButton:
        btn = _PlusButton()
        btn.setObjectName(f"PromptPresetAdd_{key}")
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedSize(24, 24)
        btn.setProperty("presetKey", key)
        btn.setToolTip("词条：新建、修改、删除")
        self._style_add_preset_btn(btn)
        btn.clicked.connect(lambda _=False, k=key: self._open_entry_manager(k))
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
        btn = _FreezeMarkButton()
        btn.setObjectName(f"PromptFreeze_{key}")
        btn.setProperty("fieldKey", key)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setFixedSize(_TITLE_ACT, _TITLE_ACT)
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
        locked = key in getattr(self, "_locked_keys", set())
        has = (not frozen) and (not locked) and self._clipboard_has_text()
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
        self._note_category_activity(key)
        if key in getattr(self, "_frozen_keys", set()) or key in getattr(self, "_locked_keys", set()):
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
        setter = getattr(btn, "set_glyph_colors", None)
        if setter is None and isinstance(btn, _PlusButton):
            setter = btn.set_plus_colors
        if callable(setter):
            setter(fg, h["hover_fg"], h["press_fg"], tk("text_dim"))

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
        locked = key in getattr(self, "_locked_keys", set())
        blocked = frozen or locked
        active = self._active_values_for(key)
        for btn in group.buttons():
            want = str(btn.property("chipValue") or "").strip() in active
            if btn.isChecked() != want:
                btn.blockSignals(True)
                btn.setChecked(want)
                btn.blockSignals(False)
            # 已写入正文时按钮是绿色，仍可点：再点一次把这段从左边删掉
            btn.setEnabled(not blocked)
            btn.setCursor(Qt.ArrowCursor if blocked else Qt.PointingHandCursor)
            self._refresh_chip_caption(btn, want)
        for btn in list(getattr(self, "_add_btns", []) or []):
            try:
                if str(btn.property("presetKey") or "") == key:
                    btn.setEnabled(not blocked)
                    btn.setCursor(Qt.ArrowCursor if blocked else Qt.PointingHandCursor)
            except RuntimeError:
                continue

    def _on_chip_clicked(self, btn: QPushButton):
        key = str(btn.property("promptKey") or "")
        value = str(btn.property("chipValue") or "").strip()
        # 1) Arm center target first (no timer yet).
        self._arm_scroll_center(btn, key, value)
        # 2) May schedule auto-collapse of other H3s.
        self._note_category_activity(key)
        # 3) If collapse queued, that flush centers after height sync.
        #    Otherwise schedule deferred center via row sync.
        collapse_keep = getattr(self, "_h3_collapse_pending", None)
        if not (isinstance(collapse_keep, str) and collapse_keep):
            self._schedule_scroll_center(key)
        if key in getattr(self, "_frozen_keys", set()) or key in getattr(self, "_locked_keys", set()):
            self._sync_field_chips(key)
            return
        if self._chip_matches(btn):
            self._remove_chip(btn)
        else:
            self._apply_chip(btn)
            # 写入正文后：物理把该条提到分类文件 / 自定义列表最前，UI 跟文件序
            self._promote_preset_chip(btn)

    def _remove_chip(self, btn: QPushButton):
        """绿色（已写入）预设再点一次：从左边正文拿掉这段，按钮恢复常态。"""
        key = str(btn.property("promptKey") or "")
        if key in getattr(self, "_frozen_keys", set()) or key in getattr(self, "_locked_keys", set()):
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
        """写入该预设；同栏互斥整栏替换——左栏正文清空后只保留本条（每栏同时只激活一条）。"""
        key = str(btn.property("promptKey") or "")
        if key in getattr(self, "_frozen_keys", set()) or key in getattr(self, "_locked_keys", set()):
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
        # 整栏替换：点词条即左栏只显示该条，不保留旧预设或自由文本残段
        ed.setPlainText(value)

    def _primary_active_value(self, key: str) -> str:
        active = self._active_values_for(key)
        if not active:
            return ""
        return max(active, key=lambda v: len(v or ""))

    def _open_entry_manager(self, key: str):
        self._note_category_activity(key)
        if not key or key in getattr(self, "_frozen_keys", set()) or key in getattr(self, "_locked_keys", set()):
            return
        dlg = _EntryManageDialog(self, self, key)
        dlg.exec_()

    def _txt_pair_list(self, key: str):
        """分类文件里这一栏的 (标题, 内容)。没有文件时返回 None。"""
        return _read_txt_category(key)

    def _entry_value_taken(self, key: str, value: str, skip_value: str = "") -> bool:
        value = (value or "").strip()
        skip_value = (skip_value or "").strip()
        if not value:
            return False
        for _src, _name, existing in self._ordered_entries(key):
            if existing == value and existing != skip_value:
                return True
        return False

    def _refresh_entry_ui(self, key: str):
        host = getattr(self, "_preset_host", None)
        if host is not None and hasattr(host, "sync_shared_presets") and not getattr(host, "_loading", False):
            host.sync_shared_presets()
        else:
            self._entry_sig.pop(key, None)
            built = self._layout_field_entries(key, force=True)
            self._after_entry_layout(key, built)
            self._schedule_persist()
        QTimer.singleShot(0, lambda k=key: self._sync_section_row(k))

    def _replace_active_entry_text(self, key: str, old_value: str, new_value: str):
        old_value = (old_value or "").strip()
        new_value = (new_value or "").strip()
        if not old_value or old_value not in self._active_values_for(key):
            return
        ed = self._left.get(key)
        if ed is not None and new_value:
            ed.setPlainText(new_value)

    def _strip_entry_text(self, key: str, value: str):
        value = (value or "").strip()
        ed = self._left.get(key)
        if ed is None or not value:
            return
        old = ed.toPlainText() or ""
        new = _strip_chip_segments(old, value, self._field_chip_values(key))
        if not (new or "").strip():
            new = ""
        if new != old:
            ed.setPlainText(new)

    def _create_prompt_entry(self, key: str, name: str, value: str) -> Tuple[bool, str]:
        name, value, err = _normalize_entry_text(name, value)
        if err:
            return False, err
        if self._entry_value_taken(key, value):
            return False, "已有相同内容的词条"
        pairs = self._txt_pair_list(key)
        if pairs is None:
            items = list(self._custom_presets.get(key) or [])
            items.insert(0, {"name": name, "value": value})
            self._custom_presets[key] = items
        else:
            if not _write_txt_category(key, [(name, value)] + pairs):
                return False, "没能写入提示词分类.txt"
        self._refresh_entry_ui(key)
        return True, f"已新建「{name}」。点这条词条就会写进左边。"

    def _update_prompt_entry(
        self, key: str, old_name: str, old_value: str, new_name: str, new_value: str,
    ) -> Tuple[bool, str]:
        old_name = (old_name or "").strip()
        old_value = (old_value or "").strip()
        new_name, new_value, err = _normalize_entry_text(new_name, new_value)
        if not old_value:
            return False, "先点一条词条"
        if err:
            return False, err
        if new_name == old_name and new_value == old_value:
            return False, "没有改动"
        if new_value != old_value and self._entry_value_taken(key, new_value, old_value):
            return False, "已有相同内容的词条"
        pairs = self._txt_pair_list(key)
        in_txt = False
        if pairs is not None:
            rewritten = []
            for n, v in pairs:
                if v == old_value:
                    in_txt = True
                    rewritten.append((new_name, new_value))
                else:
                    rewritten.append((n, v))
            if in_txt and not _write_txt_category(key, rewritten):
                return False, "没能写入提示词分类.txt"
        items = list(self._custom_presets.get(key) or [])
        custom_hit = False
        for it in items:
            if str((it or {}).get("value") or "").strip() == old_value:
                it["name"] = new_name
                it["value"] = new_value
                custom_hit = True
        if custom_hit:
            self._custom_presets[key] = items
        if not in_txt and not custom_hit:
            return False, "这条是内置预设，没有分类文件可改"
        self._replace_active_entry_text(key, old_value, new_value)
        self._refresh_entry_ui(key)
        return True, f"已修改「{new_name}」"

    def _delete_prompt_entry(self, key: str, name: str, value: str) -> Tuple[bool, str]:
        name = (name or "").strip()
        value = (value or "").strip()
        if not value:
            return False, "先点一条词条"
        pairs = self._txt_pair_list(key)
        in_txt = False
        if pairs is not None:
            kept_pairs = [(n, v) for n, v in pairs if v != value]
            in_txt = len(kept_pairs) != len(pairs)
            if in_txt and not _write_txt_category(key, kept_pairs):
                return False, "没能写入提示词分类.txt"
        items = list(self._custom_presets.get(key) or [])
        kept = [
            it for it in items
            if str((it or {}).get("value") or "").strip() != value
        ]
        custom_hit = len(kept) != len(items)
        if custom_hit:
            if kept:
                self._custom_presets[key] = kept
            else:
                self._custom_presets.pop(key, None)
        if not in_txt and not custom_hit:
            return False, "这条是内置预设，没有分类文件可改"
        self._strip_entry_text(key, value)
        self._refresh_entry_ui(key)
        return True, f"已删除「{name or '这条'}」"

    def _publish_presets(self):
        """自定义预设是各分页共用的。改完通知容器刷新每一页并落盘。"""
        host = getattr(self, "_preset_host", None)
        if host is not None and hasattr(host, "sync_shared_presets"):
            host.sync_shared_presets()
            return
        self._schedule_persist()

    def _toggle_freeze_field(self, key: str):
        self._note_category_activity(key)
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
        self._sync_section_row(key)
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
        lab = self._title_labs.get(key)
        title = lab.raw_title() if lab is not None else ""
        veil.set_caption(f"{title} 已冻结" if title else "已冻结")
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
        h = self._frozen_row_h(bar) if bar is not None else wrap.height()
        if w <= 0 or h <= 0:
            veil.hide()
            return
        veil.setGeometry(x1, 0, w, h)
        veil.show()
        veil.raise_()


    def _toggle_lock_field(self, key: str):
        self._note_category_activity(key)
        if key in self._locked_keys:
            self._locked_keys.discard(key)
        else:
            self._locked_keys.add(key)
        self._apply_lock_visual(key)
        self._schedule_persist()
        # Flush now so restart always sees locked_fields in user.txt.
        self._persist_now()

    def _apply_lock_visual(self, key: str):
        self._sync_field_chips(key)
        self._sync_section_row(key)
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
        self._place_lock_veil(key)

    def _place_lock_veil(self, key: str):
        wrap = self._sections.get(key)
        veil = self._lock_veils.get(key)
        if wrap is None or veil is None:
            return
        lab = self._title_labs.get(key)
        title = lab.raw_title() if lab is not None else ""
        veil.set_caption(f"{title} 已锁定" if title else "已锁定")
        locked = key in getattr(self, "_locked_keys", set())
        frozen = key in getattr(self, "_frozen_keys", set())
        if not locked or frozen:
            veil.hide()
            return
        w = wrap.width()
        h = wrap.height()
        if w <= 0 or h <= 0:
            veil.hide()
            return
        veil.setGeometry(0, 0, w, h)
        veil.show()
        veil.raise_()


    def _find_chip_for_value(self, key: str, value: str):
        """Return a live chip button for key/value, or None."""
        value = (value or "").strip()
        if not key or not value:
            return None
        group = self._chip_groups.get(key)
        if group is None:
            return None
        for btn in group.buttons():
            try:
                if str(btn.property("chipValue") or "").strip() == value:
                    return btn
            except RuntimeError:
                continue
        return None

    def _promote_entry_value(self, key: str, value: str, name: str = "", custom: bool = False):
        """Promote like chip click, without needing a chip widget (H3-collapsed OK)."""
        value = (value or "").strip()
        name = (name or "").strip()
        if not key or not value:
            return False
        if custom:
            items = list(self._custom_presets.get(key) or [])
            hit = None
            for i, it in enumerate(items):
                v = str((it or {}).get("value") or "").strip()
                n = str((it or {}).get("name") or "").strip()
                if v == value and (not name or n == name):
                    hit = i
                    break
            if hit is None:
                for i, it in enumerate(items):
                    if str((it or {}).get("value") or "").strip() == value:
                        hit = i
                        break
            if hit is None or hit == 0:
                return False
            item = items.pop(hit)
            items.insert(0, item)
            self._custom_presets[key] = items
            self._publish_presets()
        else:
            if not _promote_txt_preset_entry(key, value, name):
                return False
        built = self._layout_field_entries(key, force=True)
        self._after_entry_layout(key, built)
        return True

    def _restore_scroll_value(self, bar, saved):
        if bar is None or saved is None:
            return
        try:
            bar.setValue(min(int(saved), int(bar.maximum())))
        except Exception:
            pass

    def _pick_entries_random(self):
        self._pick_entries_batch(mode="random")

    def _pick_entries_sequential(self):
        self._pick_entries_batch(mode="seq")

    def _pick_entries_batch(self, mode: str):
        """Fill non-frozen/non-locked categories; promote like chip click; center full prompt."""
        scroll = getattr(self, "_scroll", None)
        bar = None
        saved = None
        if scroll is not None:
            try:
                bar = scroll.verticalScrollBar()
                if bar is not None:
                    saved = int(bar.value())
            except Exception:
                bar = None
                saved = None

        self._batch_picking = True
        self._clear_scroll_center_pending()
        self._scroll_center_gen = int(getattr(self, "_scroll_center_gen", 0) or 0) + 1
        # Drop any pending H3 auto-collapse from prior clicks.
        self._h3_collapse_pending = None

        changed = []
        try:
            frozen = getattr(self, "_frozen_keys", set())
            locked = getattr(self, "_locked_keys", set())
            for key, _title, _ph in list(self._specs):
                if key in frozen or key in locked:
                    continue
                entries = self._ordered_entries(key)
                usable = [(s, n, v) for s, n, v in entries if (v or "").strip()]
                if not usable:
                    continue
                if mode == "seq":
                    src, name, value = usable[-1]
                else:
                    cur = self._primary_active_value(key)
                    choices = [t for t in usable if t[2] != cur] or usable
                    src, name, value = random.choice(choices)
                ed = self._left.get(key)
                if ed is None:
                    continue
                # Block textChanged side-effects (H3 collapse / activity) during batch.
                ed.blockSignals(True)
                try:
                    if (ed.toPlainText() or "") != value:
                        ed.setPlainText(value)
                finally:
                    ed.blockSignals(False)
                try:
                    self._style_one_edit(ed, "blue")
                except Exception:
                    pass
                # Promote same as chip click (txt file / custom list -> position 1).
                custom = str(src or "") == "custom"
                btn = self._find_chip_for_value(key, value)
                if btn is not None:
                    self._promote_preset_chip(btn)
                else:
                    self._promote_entry_value(key, value, name, custom=custom)
                changed.append(key)

            for key in changed:
                self._sync_field_chips(key)
            if changed:
                self._refresh_full_prompt()
                self._schedule_persist()
        finally:
            self._batch_picking = False
            self._clear_scroll_center_pending()
            if changed:
                # After pick/promote: scroll main page so「完整提示词」title is vertically centered.
                # _batch_picking already blocked chip-list / H3 activity jump during promote.
                # Deferred passes cover height reflow / full-prompt layout settle.
                self._center_full_prompt_in_view()
                QTimer.singleShot(0, self._center_full_prompt_in_view)
                QTimer.singleShot(50, self._center_full_prompt_in_view)
                QTimer.singleShot(220, self._center_full_prompt_in_view)
            else:
                self._restore_scroll_value(bar, saved)
                QTimer.singleShot(0, lambda b=bar, s=saved: self._restore_scroll_value(b, s))
                QTimer.singleShot(50, lambda b=bar, s=saved: self._restore_scroll_value(b, s))
                QTimer.singleShot(220, lambda b=bar, s=saved: self._restore_scroll_value(b, s))

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
        # 1px border is outside the QSS min/max content box: use _TITLE_ACT-2 so
        # setFixedSize(_TITLE_ACT) stays 18x18 (same as page-corner buttons).
        # min=_TITLE_ACT + border:1 used to inflate the pill to 20x20 while
        # frozen-bar / order-card geometry still centered an 18px box → icon
        # looked 1px right and 1px down inside the colored rounded rect.
        inner = _TITLE_ACT - 2
        btn.setStyleSheet(
            f"QPushButton{{"
            f"min-width:{inner}px;max-width:{inner}px;"
            f"min-height:{inner}px;max-height:{inner}px;"
            f"padding:0;margin:0;border-radius:4px;"
            f"border:1px solid {bd};background:{bg};}}"
            f"QPushButton:hover{{"
            f"background:{h['hover_bg']};border-color:{h['hover_bd']};}}"
            f"QPushButton:pressed{{"
            f"background:{h['press_bg']};border-color:{h['press_bd']};}}"
        )
        mark = getattr(btn, "set_mark", None)
        if callable(mark):
            btn.setIcon(QIcon())
            mark(frozen, ico)
        else:
            btn.setIcon(_freeze_icon(frozen, _PASTE_ICON, ico))

    def _make_full_prompt_section(self) -> QWidget:
        wrap = _PromptSection(self, "full")
        wrap.setObjectName("PromptFullSection")
        wrap.setAttribute(Qt.WA_StyledBackground, True)
        wrap.setStyleSheet("background:transparent;border:none;")
        root = QVBoxLayout(wrap)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        root.setAlignment(Qt.AlignTop)

        cols = QWidget()
        cols.setObjectName("PromptFullCols")
        cols.setAttribute(Qt.WA_StyledBackground, True)
        cols.setStyleSheet("background:transparent;border:none;")
        cols.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        col_lay = QHBoxLayout(cols)
        col_lay.setContentsMargins(0, 0, 0, 0)
        col_lay.setSpacing(6)

        def _one_col(tint: str, key: str, ph: str, lang: str):
            col = QWidget()
            col.setAttribute(Qt.WA_StyledBackground, True)
            col.setStyleSheet("background:transparent;border:none;")
            col.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            v = QVBoxLayout(col)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(6)
            bar, title_lab = self._make_full_col_bar(lang)
            edit = self._make_edit(tint, key, ph, persist=False)
            edit.setReadOnly(True)
            edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            edit._after_fit = lambda: self._sync_full_prompt_layout()
            v.addWidget(bar, 0)
            v.addWidget(edit, 0)
            return col, edit, bar, title_lab

        # 左右对调：英(绿)在左、中(蓝)在右；色随内容走
        # 标题：完整提示词 + 状态；开关；复制按钮（只复制这一栏）
        en_col, edit_en, bar_en, lab_en = _one_col(
            "green", "full_en", "英文完整提示词", "en"
        )
        zh_col, edit_zh, bar_zh, lab_zh = _one_col(
            "blue", "full_zh", "中文完整提示词", "zh"
        )
        self._full_prompt_zh = edit_zh
        self._full_prompt_en = edit_en
        self._full_prompt = edit_zh
        self._full_col_zh = zh_col
        self._full_col_en = en_col
        self._full_prompt_bars = (bar_en, bar_zh)
        self._full_prompt_labs = {"en": lab_en, "zh": lab_zh}
        self._full_prompt_bar = bar_en
        self._full_prompt_lab = lab_en
        self._full_cols = cols
        self._full_wrap = wrap
        self._full_parts_sig_zh = None
        self._full_parts_sig_en = None

        col_lay.addWidget(en_col, 1)
        col_lay.addWidget(zh_col, 1)
        root.addWidget(cols, 0)
        self._style_full_title_bar()
        QTimer.singleShot(0, self._refresh_full_prompt)
        return wrap

    def _make_full_col_bar(self, lang: str):
        """一列标题：完整提示词 + 状态，翻译开关，复制按钮。"""
        lang = "en" if lang == "en" else "zh"
        bar = QFrame()
        bar.setObjectName(f"PromptFullBar_{lang}")
        bar.setFrameShape(QFrame.NoFrame)
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setAutoFillBackground(True)
        bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        bar.setFixedHeight(_COPY_H)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(8)
        title = QLabel("完整提示词 原文")
        title.setObjectName(f"PromptFullTitle_{lang}")
        title.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        chk = self._make_translate_lang_chk(lang)
        copy_btn = self._make_copy_lang_btn(lang)
        lay.addStretch(1)
        lay.addWidget(title, 0, Qt.AlignVCenter)
        lay.addWidget(chk, 0, Qt.AlignVCenter)
        lay.addWidget(copy_btn, 0, Qt.AlignVCenter)
        lay.addStretch(1)
        return bar, title

    def _style_full_title_bar(self):
        bg, fg = "#202020", "#e4e4e7"
        bars = list(getattr(self, "_full_prompt_bars", None) or [])
        if not bars:
            one = getattr(self, "_full_prompt_bar", None)
            if one is not None:
                bars = [one]
        for bar in bars:
            if bar is None:
                continue
            oid = bar.objectName() or "PromptFullBar"
            bar.setStyleSheet(
                f"QFrame#{oid}{{"
                f"background:{bg};border:none;border-radius:4px;}}"
            )
            bar.setFixedHeight(_COPY_H)
            pal = bar.palette()
            pal.setColor(QPalette.Window, QColor(bg))
            pal.setColor(QPalette.Base, QColor(bg))
            bar.setPalette(pal)
        labs_map = getattr(self, "_full_prompt_labs", None) or {}
        if isinstance(labs_map, dict):
            labs = list(labs_map.values())
        else:
            labs = list(labs_map)
        if not labs:
            one = getattr(self, "_full_prompt_lab", None)
            if one is not None:
                labs = [one]
        for lab in labs:
            if lab is None:
                continue
            oid = lab.objectName() or "PromptFullTitle"
            lab.setStyleSheet(
                f"QLabel#{oid}{{color:{fg};background:transparent;border:none;"
                f"font-size:13px;font-weight:700;}}"
            )
        for attr in ("btn_copy_en", "btn_copy_zh"):
            btn = getattr(self, attr, None)
            if btn is not None:
                self._style_full_copy_btn(btn)

    def _sync_full_prompt_layout(self):
        wrap = getattr(self, "_full_wrap", None)
        edit_zh = getattr(self, "_full_prompt_zh", None)
        edit_en = getattr(self, "_full_prompt_en", None)
        if wrap is None or edit_zh is None or edit_en is None:
            return
        if getattr(self, "_syncing_full", False):
            return
        self._syncing_full = True
        try:
            total = max(1, int(wrap.width()))
            gap = 6
            col_w = max(1, (total - gap) // 2)
            for ed in (edit_zh, edit_en):
                ed.setMaximumWidth(16777215)
                ed.setMinimumWidth(0)
                ed.setFixedWidth(col_w)
            # 宽度落定后再读 viewport；仍不可靠则用列宽减 chrome
            vw = int(edit_zh.viewport().width())
            if vw <= 8:
                vw = max(1, col_w - 2 * int(edit_zh.frameWidth()) - 2 * _EDIT_PAD_H)
            parts_zh = self._preview_parts_for_lang("zh")
            parts_en = self._preview_parts_for_lang("en")
            h_zh = int(self._full_prompt_needed_h(edit_zh, vw, parts_zh))
            h_en = int(self._full_prompt_needed_h(edit_en, vw, parts_en))
            h = max(h_zh, h_en)
            edit_zh.apply_height(h)
            edit_en.apply_height(h)
            cols = getattr(self, "_full_cols", None)
            # 每列自带标题栏：列高 = 标题 + 间距 + 编辑区
            bar_h = _COPY_H
            for bar in list(getattr(self, "_full_prompt_bars", None) or []):
                if bar is not None and int(bar.height() or 0) > 0:
                    bar_h = max(bar_h, int(bar.height()))
            block_h = bar_h + 6 + h
            if cols is not None:
                cols.setMinimumHeight(block_h)
                cols.setFixedHeight(block_h)
            for col in (
                getattr(self, "_full_col_zh", None),
                getattr(self, "_full_col_en", None),
            ):
                if col is not None:
                    col.setFixedWidth(col_w)
                    col.setMinimumHeight(block_h)
                    col.setFixedHeight(block_h)
            # 整段最小高度就是双列（标题已在列内）。勿 adjustSize，会把 scroll host 缩矮
            try:
                wrap_h = block_h
                wrap.setMinimumHeight(wrap_h)
                wrap.updateGeometry()
                scroll = getattr(self, "_scroll", None)
                if scroll is not None:
                    host = scroll.widget()
                    if host is not None:
                        host.updateGeometry()
            except Exception:
                pass
        finally:
            self._syncing_full = False

    def _compose_parts(self, side: str) -> List[Tuple[str, str]]:
        """按分区顺序返回 (key, text)，规则与 _compose 一致。"""
        edits = self._left if side == "orig" else self._right
        parts: List[Tuple[str, str]] = []
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
                parts.append((key, text))
        return parts

    def _full_line_px(self, edit: QPlainTextEdit) -> int:
        """分类正文行高：默认行距 × 1.5（FixedHeight）。"""
        return max(1, int(edit.fontMetrics().lineSpacing() * 1.5 + 0.5))

    def _full_section_gap_px(self, edit: QPlainTextEdit) -> int:
        """分类间距目标像素（约 1/4 正文行高 ≈6–8px；相对旧半行再减半）。

        QPlainTextEdit 常忽略 FixedHeight；用更小字号的 NBSP 分隔块逼近半行距视觉高度。
        测高按 gap 块 ≈ 0.5×默认 lineSpacing。
        """
        return max(6, int(self._full_line_px(edit) * 0.25 + 0.5))

    def _full_prompt_needed_h(
        self,
        edit: QPlainTextEdit,
        text_width: int = 0,
        parts: List[Tuple[str, str]] = None,
    ) -> int:
        """完整提示词编辑区高度：用独立 QTextDocument 测高（勿信 live PTE layout）。

        根因：QPlainTextEdit 使用 QPlainTextDocumentLayout，在控件偏矮时
        documentSize / blockBoundingRect 常≈当前视口高（约十余行），若以此
        为唯一依据再 setFixedHeight，会把双列锁死在短视口，主页滚不到全文。

        规则（与 _GrowEdit._content_h 一致）：
        1) 用 parts 填入离线 QTextDocument（默认 QTextDocumentLayout）+ setTextWidth
        2) measured = documentSize / size
        3) measured 有效则用之；否则 formula / 行数兜底
        4) h = measured + ~1.25×ls 底垫 + chrome
        """
        vw = int(text_width) if text_width > 0 else int(edit.viewport().width())
        if vw <= 0:
            return edit._min_h()
        if parts is None:
            parts = self._preview_parts_for_lang("zh")
        ls = float(edit.fontMetrics().lineSpacing())
        line_px = float(self._full_line_px(edit))
        gap_px = float(self._full_section_gap_px(edit))
        fm = edit.fontMetrics()
        chrome = float(edit._chrome())
        bottom_pad = max(10.0, ls * 1.25)

        def _measure_standalone(doc: QTextDocument) -> float:
            if doc is None:
                return 0.0
            try:
                doc.setTextWidth(float(max(1, vw)))
            except Exception:
                pass
            try:
                lay = doc.documentLayout()
                if lay is not None:
                    h = float(lay.documentSize().height())
                    if h > 1.0:
                        return h
            except Exception:
                pass
            try:
                h = float(doc.size().height())
                return h if h > 1.0 else 0.0
            except Exception:
                return 0.0

        def _formula_h(doc: QTextDocument) -> float:
            if doc is None:
                return 0.0
            total = 0.0
            b = doc.begin()
            w = max(1.0, float(vw))
            while b.isValid():
                txt = b.text()
                if (not txt) or txt == "\u00a0":
                    total += gap_px  # 与半行视觉 gap 对齐，避免公式虚高
                else:
                    try:
                        fw = float(fm.horizontalAdvance(txt))
                    except Exception:
                        fw = float(fm.width(txt))
                    wraps = max(1, int(fw / w + 0.999))
                    total += wraps * line_px
                b = b.next()
            return total

        # 永远用离线文档测高；绝不读 live QPlainTextDocumentLayout
        meas = QTextDocument()
        meas.setDefaultFont(edit.font())
        meas.setDocumentMargin(0)
        if parts:
            self._fill_full_prompt_doc(meas, parts, edit)
        else:
            plain = (edit.toPlainText() or "").strip()
            if plain:
                meas.setPlainText(plain)
            else:
                return edit._min_h()

        measured = _measure_standalone(meas)
        # 独立文档实测为准（与屏幕 FixedHeight 一致）；公式仅当实测失败
        # 勿 max(measured, formula)：手工折行常虚高，会再现大片空白
        h_content = measured
        if h_content <= 1.0:
            h_content = _formula_h(meas)
        if h_content <= 1.0 and parts:
            n = 0
            for _k, t in parts:
                n += max(1, str(t).count("\n") + 1)
            n += max(0, len(parts) - 1)
            h_content = float(n) * line_px
        if h_content <= 1.0:
            return edit._min_h()

        h = int(h_content + bottom_pad + chrome + 0.5)
        return max(edit._min_h(), h)

    def _fill_full_prompt_doc(
        self, doc: QTextDocument, parts: List[Tuple[str, str]], edit: QPlainTextEdit
    ) -> None:
        """分类着色写入文档。翻译状态在底栏标签，不进文档。

        分类间距：小号字 NBSP 分隔块，视觉约半个默认行距（相对旧整行空行再减半）。
        """
        line_px = self._full_line_px(edit)
        gap_px = self._full_section_gap_px(edit)
        body_fmt = QTextBlockFormat()
        try:
            body_fmt.setLineHeight(line_px, QTextBlockFormat.FixedHeight)
        except Exception:
            pass
        body_fmt.setTopMargin(0)
        body_fmt.setBottomMargin(0)
        try:
            body_fmt.setAlignment(Qt.AlignHCenter)
        except Exception:
            pass
        gap_fmt = QTextBlockFormat()
        try:
            gap_fmt.setLineHeight(float(gap_px), QTextBlockFormat.FixedHeight)
        except Exception:
            pass
        gap_fmt.setTopMargin(0)
        gap_fmt.setBottomMargin(0)
        try:
            gap_fmt.setAlignment(Qt.AlignHCenter)
        except Exception:
            pass
        gap_char = QTextCharFormat()
        gap_char.setForeground(QColor(0, 0, 0, 1))  # 几乎透明，只占位
        # 用约半号字体压低分隔块行高（PlainTextEdit 对 FixedHeight 不可靠）
        try:
            gf = QFont(edit.font())
            ps = gf.pointSizeF()
            if ps > 0:
                gf.setPointSizeF(max(1.0, ps * 0.5))
            else:
                px = max(1, int(edit.fontMetrics().height() * 0.5))
                gf.setPixelSize(px)
            gap_char.setFont(gf)
        except Exception:
            pass
        cursor = QTextCursor(doc)
        if not parts:
            return
        for si, (key, text) in enumerate(parts):
            if si > 0:
                cursor.insertBlock(gap_fmt)
                cursor.insertText("\u00a0", gap_char)
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(_field_accent(key)))
            chunks = str(text).split("\n")
            for ci, chunk in enumerate(chunks):
                if si == 0 and ci == 0:
                    cursor.setBlockFormat(body_fmt)
                    cursor.insertText(chunk, fmt)
                else:
                    cursor.insertBlock(body_fmt)
                    cursor.insertText(chunk, fmt)

    def _apply_full_prompt_document(self, edit: QPlainTextEdit, parts: List[Tuple[str, str]]):
        """写入分类着色 + 分类空行间距。"""
        edit.blockSignals(True)
        try:
            edit.clear()
            doc = edit.document()
            doc.setDocumentMargin(0)
            self._fill_full_prompt_doc(doc, parts, edit)
        finally:
            edit.blockSignals(False)

    def _tr_cache_get(self) -> Dict[Tuple[str, str, str], str]:
        cache = getattr(self, "_tr_cache", None)
        if cache is None:
            self._tr_cache = {}
            cache = self._tr_cache
        return cache


    def _gloss_zh_en_if_needed(self, lang: str, text: str) -> str:
        """英栏译后过一遍服饰错译表（与 translate_job 同规则）。"""
        if lang != "en" or not text:
            return text
        try:
            from utils.translate_job import _post_zh_en_glossary
            return _post_zh_en_glossary(text)
        except Exception:
            return text

    def _opus_direction_for_lang(self, lang: str) -> str:
        """英栏 → zh-en；中栏 → en-zh。"""
        return "zh-en" if lang == "en" else "en-zh"

    def _tr_out_still_source_lang(self, lang: str, src: str, out: str) -> bool:
        """英栏译文仍大量汉字 / 中栏译文仍是拉丁主导 → 视为未译成功。"""
        from utils.translate_job import _han_ratio, _mostly_latin

        out = (out or "").strip()
        src = (src or "").strip()
        if not out:
            return True
        if lang == "en":
            # 目标是英文：残留较多汉字，或几乎原样中文
            if _han_ratio(out) >= 0.18:
                return True
            if out == src and _han_ratio(src) >= 0.08:
                return True
            return False
        # lang == zh：目标中文
        if _han_ratio(out) < 0.08 and _mostly_latin(out):
            return True
        if out == src and _mostly_latin(src):
            return True
        return False

    def _purge_invalid_tr_cache(self, lang: str = None) -> int:
        """丢掉「当成译文缓存的原文/未译成功」条目，迫使重译。"""
        cache = self._tr_cache_get()
        n = 0
        for ck in list(cache.keys()):
            if not (isinstance(ck, tuple) and len(ck) == 3):
                cache.pop(ck, None)
                n += 1
                continue
            lg, _key, src = ck
            if lang is not None and lg != lang:
                continue
            out = cache.get(ck)
            if self._tr_out_still_source_lang(lg, src, str(out or "")):
                cache.pop(ck, None)
                n += 1
        return n

    def _preview_parts_for_lang(self, lang: str) -> List[Tuple[str, str]]:
        """按语种从缓存生成完整提示词分段（源文为左侧字段组装）。

        对应语种翻译开关关闭时直接返回源文，不读译缓存。
        英栏开启时：无效缓存（仍是中文）当作未命中，显示原文并继续排队翻译。
        """
        src_parts = self._compose_parts("orig")
        lang = "en" if lang == "en" else "zh"
        if not self._translate_lang_is_enabled(lang):
            return list(src_parts)
        cache = self._tr_cache_get()
        out: List[Tuple[str, str]] = []
        for key, text in src_parts:
            hit = cache.get((lang, key, text))
            if hit is not None and self._tr_out_still_source_lang(lang, text, str(hit)):
                # 脏缓存：删掉，当未译
                cache.pop((lang, key, text), None)
                hit = None
            out.append((key, hit if hit is not None else text))
        return out

    def _invalidate_tr_cache_for_sources(self, src_parts: List[Tuple[str, str]]) -> None:
        """源文变化时丢掉过期缓存项（任意语种）。"""
        cache = self._tr_cache_get()
        if not cache:
            return
        alive = {(k, s) for k, s in src_parts}
        for ck in list(cache.keys()):
            if not (isinstance(ck, tuple) and len(ck) == 3):
                cache.pop(ck, None)
                continue
            _lang, key, src = ck
            if (key, src) not in alive:
                cache.pop(ck, None)

    def _cancel_tr_worker(self) -> None:
        w = getattr(self, "_tr_worker", None)
        self._tr_worker = None
        if w is None:
            return
        try:
            w.finished_ok.disconnect()
        except Exception:
            pass
        try:
            w.finished_err.disconnect()
        except Exception:
            pass
        try:
            if w.isRunning():
                w.requestInterruption()
        except Exception:
            pass

    def _lang_missing(self, src_parts: List[Tuple[str, str]], lang: str) -> bool:
        cache = self._tr_cache_get()
        lang = "en" if lang == "en" else "zh"
        for k, s in src_parts:
            ck = (lang, k, s)
            if ck not in cache:
                return True
            if self._tr_out_still_source_lang(lang, s, str(cache.get(ck) or "")):
                return True
        return False

    def _seed_skip_cache(self, src_parts: List[Tuple[str, str]], lang: str) -> None:
        from utils.translate_job import _should_skip

        direction = self._opus_direction_for_lang(lang)
        cache = self._tr_cache_get()
        for key, text in src_parts:
            ck = (lang, key, text)
            if ck in cache:
                continue
            if _should_skip(text, direction):
                cache[ck] = text

    def _start_lang_translate(
        self, src_parts: List[Tuple[str, str]], lang: str, *, quiet: bool = False,
    ) -> None:
        """缺译分段送进 opus-MT；中栏=en-zh，英栏=zh-en。该语种开关关闭时不加载。"""
        lang = "en" if lang == "en" else "zh"
        if not self._translate_lang_is_enabled(lang):
            self._tr_status = "off"
            self._tr_busy_lang = ""
            if not quiet:
                tag = "翻译英文" if lang == "en" else "翻译中文"
                message_box_warn(
                    self,
                    "中英翻译",
                    f"「{tag}」开关未打开。\n\n请先勾选这一栏标题上的开关，再翻译该栏。",
                )
            self._update_tr_status_label()
            return
        from utils.translate_opus import (
            opus_direction_ready,
            make_translate_worker,
        )
        from utils.translate_job import _should_skip

        lang = "en" if lang == "en" else "zh"
        direction = self._opus_direction_for_lang(lang)
        if not opus_direction_ready(direction):
            self._tr_status = "missing"
            self._tr_busy_lang = lang
            model_name = "opus-mt-zh-en" if direction == "zh-en" else "opus-mt-en-zh"
            arrow = "中→英" if direction == "zh-en" else "英→中"
            message_box_warn(
                self,
                "中英翻译",
                f"还没有 opus-MT {arrow} 模型。\n\n"
                f"请到「系统总览 → 模型」按教程下载 {model_name}，"
                f"或运行：\npython tools/download_models.py --only {model_name}",
            )
            self._update_tr_status_label()
            return

        cache = self._tr_cache_get()
        need_keys: List[str] = []
        need_texts: List[str] = []
        for key, text in src_parts:
            ck = (lang, key, text)
            if ck in cache:
                continue
            if _should_skip(text, direction):
                cache[ck] = text
                continue
            need_keys.append(key)
            need_texts.append(text)
        if not need_texts:
            return

        self._cancel_tr_worker()
        self._tr_req_id = int(getattr(self, "_tr_req_id", 0) or 0) + 1
        req_id = self._tr_req_id
        self._tr_status = "translating"
        self._tr_busy_lang = lang
        Worker = make_translate_worker()
        worker = Worker(need_texts, direction, force_cpu=False, parent=self)
        self._tr_worker = worker

        def _ok(
            outs: list,
            _direction: str,
            rid=req_id,
            keys=list(need_keys),
            srcs=list(need_texts),
            want_lang=lang,
        ):
            if rid != getattr(self, "_tr_req_id", 0):
                return
            cache = self._tr_cache_get()
            from utils.translate_job import _degenerate_translation

            pending = False
            for k, s, o in zip(keys, srcs, outs):
                text = str(o or "")
                ck = (want_lang, k, s)
                if (not text) or _degenerate_translation(s, text):
                    log.warning(
                        "opus-MT degenerate lang=%s field=%s src=%r out=%r — do not cache",
                        want_lang,
                        k,
                        (s or "")[:80],
                        text[:80],
                    )
                    cache.pop(ck, None)
                    pending = True
                    continue
                if want_lang == "en":
                    text = self._gloss_zh_en_if_needed("en", text)
                if self._tr_out_still_source_lang(want_lang, s, text):
                    log.warning(
                        "opus-MT leftover-source lang=%s field=%s out=%r — do not cache",
                        want_lang,
                        k,
                        text[:80],
                    )
                    cache.pop(ck, None)
                    pending = True
                    continue
                cache[ck] = text
            self._tr_worker = None
            self._tr_busy_lang = ""
            if pending:
                self._tr_status = "translating"
                self._full_parts_sig_en = None
                self._full_parts_sig_zh = None
                self._refresh_full_prompt()
                # 稍后重试缺译段（模型偶发退回原文）
                QTimer.singleShot(400, self._refresh_full_prompt)
            else:
                self._tr_status = "ready"
                self._full_parts_sig_en = None
                self._full_parts_sig_zh = None
                self._refresh_full_prompt()
            self._update_translate_switch_tips()

        def _err(msg: str, _direction: str, rid=req_id):
            if rid != getattr(self, "_tr_req_id", 0):
                return
            self._tr_status = "error:" + str(msg or "")[:120]
            self._tr_busy_lang = ""
            self._tr_worker = None
            self._update_tr_status_label()
            try:
                message_box_warn(self, "中英翻译", f"翻译失败：\n{msg}")
            except Exception:
                pass

        worker.finished_ok.connect(_ok)
        worker.finished_err.connect(_err)
        worker.start()
        self._update_tr_status_label()

    def _ensure_dual_translates(self, src_parts: List[Tuple[str, str]]) -> None:
        """仅对已打开的语种开关填 skip / 排队翻译（一次一个 worker）。"""
        langs = [L for L in ("en", "zh") if self._translate_lang_is_enabled(L)]
        if not langs:
            self._tr_status = "off"
            self._tr_busy_lang = ""
            return
        for lang in langs:
            self._purge_invalid_tr_cache(lang)
            self._seed_skip_cache(src_parts, lang)
        w = getattr(self, "_tr_worker", None)
        busy = getattr(self, "_tr_status", "") == "translating"
        if busy and w is not None:
            try:
                if w.isRunning():
                    return
            except Exception:
                pass
            # worker 已死但状态卡在 translating
            self._tr_worker = None
            self._tr_status = ""
        elif busy and w is None:
            self._tr_status = ""
        for lang in langs:
            if self._lang_missing(src_parts, lang):
                self._start_lang_translate(src_parts, lang, quiet=True)
                return
        self._tr_status = "ready" if src_parts else ""
        self._tr_busy_lang = ""

    def _plain_from_parts(self, parts: List[Tuple[str, str]]) -> str:
        return "\n".join(text for _k, text in parts)

    def _refresh_full_prompt(self):
        edit_zh = getattr(self, "_full_prompt_zh", None)
        edit_en = getattr(self, "_full_prompt_en", None)
        if edit_zh is None or edit_en is None:
            return
        if getattr(self, "_loading", False):
            return
        src_parts = self._compose_parts("orig")
        self._invalidate_tr_cache_for_sources(src_parts)
        self._ensure_dual_translates(src_parts)
        parts_zh = self._preview_parts_for_lang("zh")
        parts_en = self._preview_parts_for_lang("en")
        sig_zh = ("zh", tuple(k for k, _t in parts_zh), self._plain_from_parts(parts_zh))
        sig_en = ("en", tuple(k for k, _t in parts_en), self._plain_from_parts(parts_en))
        if getattr(self, "_full_parts_sig_zh", None) != sig_zh:
            self._apply_full_prompt_document(edit_zh, parts_zh)
            self._full_parts_sig_zh = sig_zh
            self._style_one_edit(edit_zh, "blue")
        if getattr(self, "_full_parts_sig_en", None) != sig_en:
            self._apply_full_prompt_document(edit_en, parts_en)
            self._full_parts_sig_en = sig_en
            self._style_one_edit(edit_en, "green")
        self._update_tr_status_label()
        self._sync_full_prompt_layout()
        # 宽度落定后再算一次（离线文档测高，不依赖 PTE 视口）
        QTimer.singleShot(0, self._sync_full_prompt_layout)

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
        if _is_full_prompt_key(key):
            edit.setPlaceholderText(placeholder or "")
        else:
            edit.setPlaceholderText("")
            edit.setToolTip("")
        edit.textChanged.connect(lambda _ed=edit, _t=tint: self._style_one_edit(_ed, _t))
        if persist:
            edit.textChanged.connect(self._schedule_persist)
            edit.textChanged.connect(lambda *_a, _k=key: self._note_category_activity(_k))
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


    def _host_translate_model(self) -> str:
        host = getattr(self, "_preset_host", None)
        if host is not None:
            return str(getattr(host, "_translate_model", "") or "")
        return str(getattr(self, "_translate_model", "") or "")

    def _set_host_translate_model(self, model_id: str):
        model_id = str(model_id or "").strip()
        host = getattr(self, "_preset_host", None)
        if host is not None:
            host._translate_model = model_id
        self._translate_model = model_id

    def _translate_lang_is_enabled(self, lang: str) -> bool:
        """完整提示词标题上的「英文」/「中文」分开关；默认关，不落盘。"""
        lang = "en" if lang == "en" else "zh"
        attr = "_translate_en_enabled" if lang == "en" else "_translate_zh_enabled"
        host = getattr(self, "_preset_host", None)
        if host is not None:
            return bool(getattr(host, attr, False))
        return bool(getattr(self, attr, False))

    def _translate_is_enabled(self) -> bool:
        """任一侧翻译开关打开即视为启用（模型列表可交互）。"""
        return self._translate_lang_is_enabled("en") or self._translate_lang_is_enabled("zh")

    def _set_host_translate_lang_enabled(self, lang: str, on: bool):
        lang = "en" if lang == "en" else "zh"
        attr = "_translate_en_enabled" if lang == "en" else "_translate_zh_enabled"
        on = bool(on)
        host = getattr(self, "_preset_host", None)
        if host is not None:
            setattr(host, attr, on)
        setattr(self, attr, on)
        # 兼容旧字段：任一侧开则为 True
        any_on = self._translate_is_enabled()
        if host is not None:
            host._translate_enabled = any_on
        self._translate_enabled = any_on

    def _make_tr_status_label(self) -> QLabel:
        lab = QLabel("")
        lab.setObjectName("PromptTrStatus")
        lab.setAttribute(Qt.WA_StyledBackground, True)
        lab.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        lab.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        lab.setFixedHeight(_COPY_H)
        self.lbl_tr_status = lab
        self._update_tr_status_label()
        return lab

    def _tr_status_text(self) -> str:
        if not self._translate_is_enabled():
            return ""
        st = str(getattr(self, "_tr_status", "") or "")
        busy = str(getattr(self, "_tr_busy_lang", "") or "")
        if st == "translating":
            tag = "中栏" if busy == "zh" else "英栏"
            return f"〔{tag} · 翻译中…〕"
        if st.startswith("error:"):
            return "〔翻译失败，暂显示原文〕"
        if st == "missing":
            return "〔缺少 opus-MT 模型〕"
        if st == "off":
            return "〔翻译关闭〕"
        if st == "ready":
            return ""  # 底栏已去掉状态提示
        return ""

    def _full_col_status_word(self, lang: str) -> str:
        """标题状态：关=原文，进行中=翻译中，该栏译完=英文或中文。"""
        lang = "en" if lang == "en" else "zh"
        if not self._translate_lang_is_enabled(lang):
            return "原文"
        src_parts = self._compose_parts("orig")
        if not src_parts:
            return "原文"
        if not self._lang_missing(src_parts, lang):
            return "英文" if lang == "en" else "中文"
        st = str(getattr(self, "_tr_status", "") or "")
        busy = str(getattr(self, "_tr_busy_lang", "") or "")
        if st == "missing" or st.startswith("error"):
            if not busy or busy == lang:
                return "原文"
        return "翻译中"

    def _update_full_title_status(self) -> None:
        labs = getattr(self, "_full_prompt_labs", None) or {}
        if not isinstance(labs, dict):
            return
        for lang, lab in labs.items():
            if lab is None:
                continue
            word = self._full_col_status_word(lang)
            lab.setText(f"完整提示词 {word}")

    def _update_tr_status_label(self) -> None:
        self._update_full_title_status()
        lab = getattr(self, "lbl_tr_status", None)
        if lab is None:
            self._update_translate_switch_tips()
            return
        lab.setText(self._tr_status_text())
        lab.setStyleSheet(
            f"QLabel#PromptTrStatus{{color:{tk('text_dim')};background:transparent;"
            f"border:none;font-size:12px;padding:0 4px;}}"
        )
        self._update_translate_switch_tips()

    def _update_translate_switch_tips(self) -> None:
        """底栏已无状态标签：把 translating/missing/ready 写到开关 tip。"""
        st = str(getattr(self, "_tr_status", "") or "")
        busy = str(getattr(self, "_tr_busy_lang", "") or "")
        for lang, attr, base in (
            ("en", "chk_translate_en", "翻译英文：中→英，仅当前会话。"),
            ("zh", "chk_translate_zh", "翻译中文：英→中，仅当前会话。"),
        ):
            chk = getattr(self, attr, None)
            if chk is None:
                continue
            on = self._translate_lang_is_enabled(lang)
            extra = ""
            if not on:
                extra = "（关）"
            elif st == "translating" and (busy == lang or not busy):
                extra = "（翻译中…）"
            elif st == "missing":
                extra = "（缺少 opus-MT 模型）"
            elif st.startswith("error:"):
                extra = "（翻译失败）"
            elif st == "ready" and on:
                extra = "（已译）"
            chk.setToolTip(base + extra)

    def _make_copy_lang_btn(self, lang: str) -> QPushButton:
        """标题上的复制钮：只把这一栏正文放进剪贴板。"""
        lang = "en" if lang == "en" else "zh"
        text = "复制英文" if lang == "en" else "复制中文"
        btn = QPushButton(text)
        btn.setObjectName(f"PromptFullAct_copy_{lang}")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setToolTip("复制这一栏当前显示的完整提示词")
        self._style_full_copy_btn(btn)
        if lang == "en":
            self.btn_copy_en = btn
            btn.clicked.connect(self._copy_full_en)
        else:
            self.btn_copy_zh = btn
            btn.clicked.connect(self._copy_full_zh)
        return btn

    def _style_full_copy_btn(self, btn: QPushButton):
        h = _PromptSheet._hot_colors()
        bh = max(22, _COPY_H - 8)
        text = btn.text() or "复制"
        try:
            need = int(btn.fontMetrics().horizontalAdvance(text) + 20)
        except Exception:
            need = 76
        btn.setFixedHeight(bh)
        btn.setFixedWidth(max(need, 76))
        btn.setStyleSheet(
            f"QPushButton#{btn.objectName() or 'PromptFullAct_copy'}{{"
            f"min-height:{bh}px;max-height:{bh}px;"
            f"padding:0 8px;border-radius:6px;"
            f"border:1px solid {tk('border')};"
            f"background:{tk('input_bg')};color:{tk('text')};"
            f"font-size:12px;font-weight:700;}}"
            f"QPushButton#{btn.objectName() or 'PromptFullAct_copy'}:hover{{"
            f"background:{h['hover_bg']};border-color:{h['hover_bd']};color:{h['hover_fg']};}}"
            f"QPushButton#{btn.objectName() or 'PromptFullAct_copy'}:pressed{{"
            f"background:{h['press_bg']};border-color:{h['press_bd']};color:{h['press_fg']};}}"
        )

    def _make_full_action_btn(self, kind: str) -> QPushButton:
        labels = {
            "jump": "跳转完整提示词",
        }
        text = labels.get(kind, kind)
        btn = QPushButton(text)
        btn.setObjectName(f"PromptFullAct_{kind}")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setFixedHeight(_COPY_H)
        if kind == "jump":
            self.btn_jump_full = btn
            btn.clicked.connect(self._jump_to_full_prompt)
            self._style_jump_btn(btn)
            return btn
        btn.setStyleSheet(self._copy_all_qss())
        try:
            need = int(btn.fontMetrics().horizontalAdvance(text) + 40)
        except Exception:
            need = 108
        btn.setFixedWidth(max(need, 100))
        return btn

    def _style_jump_btn(self, btn: QPushButton = None):
        """底栏「跳转完整提示词」：蓝底白字。"""
        btn = btn or getattr(self, "btn_jump_full", None)
        if btn is None:
            return
        text = btn.text() or "跳转完整提示词"
        bh = _COPY_H
        try:
            need = int(btn.fontMetrics().horizontalAdvance(text) + 28)
        except Exception:
            need = 148
        btn.setFixedHeight(bh)
        btn.setFixedWidth(max(need, 132))
        name = btn.objectName() or "PromptFullAct_jump"
        bg, bd, fg = "#2563eb", "#1d4ed8", "#ffffff"
        btn.setStyleSheet(
            f"QPushButton#{name}{{"
            f"min-height:{bh}px;max-height:{bh}px;"
            f"padding:0 12px;border-radius:6px;"
            f"border:1px solid {bd};"
            f"background:{bg};color:{fg};"
            f"font-size:13px;font-weight:700;text-align:center;}}"
            f"QPushButton#{name}:hover{{"
            f"background:#3b82f6;border-color:#60a5fa;color:{fg};}}"
            f"QPushButton#{name}:pressed{{"
            f"background:#1d4ed8;border-color:#1e3a8a;color:{fg};}}"
        )

    def _full_column_clipboard_text(self, edit: QPlainTextEdit) -> str:
        """去掉 NBSP 分隔占位，保留真实空行折叠为单空行。"""
        if edit is None:
            return ""
        raw = edit.toPlainText() or ""
        lines = []
        for ln in raw.splitlines():
            s = ln.replace("\u00a0", "").strip()
            if s:
                lines.append(s)
        return "\n".join(lines)

    def _copy_full_zh(self):
        text = self._full_column_clipboard_text(getattr(self, "_full_prompt_zh", None))
        if not text.strip():
            text = self._plain_from_parts(self._preview_parts_for_lang("zh"))
        if not text.strip():
            return
        QApplication.clipboard().setText(_fullwidth_symbols_to_halfwidth(text))

    def _copy_full_en(self):
        text = self._full_column_clipboard_text(getattr(self, "_full_prompt_en", None))
        if not text.strip():
            text = self._plain_from_parts(self._preview_parts_for_lang("en"))
        if not text.strip():
            return
        QApplication.clipboard().setText(_fullwidth_symbols_to_halfwidth(text))

    def _center_full_prompt_in_view(self):
        """Scroll PromptScroll so「完整提示词」title row sits near vertical center."""
        target = None
        for bar in list(getattr(self, "_full_prompt_bars", None) or []):
            if bar is None:
                continue
            try:
                if bar.isHidden():
                    continue
                target = bar
                break
            except RuntimeError:
                continue
        if target is None:
            lab = getattr(self, "_full_prompt_lab", None)
            if lab is not None:
                try:
                    lab.objectName()
                    target = lab
                except RuntimeError:
                    target = None
        if target is None:
            target = getattr(self, "_full_wrap", None)
        if target is None:
            return
        self._center_widget_in_scroll(target)

    def _jump_to_full_prompt(self):
        """把「完整提示词」标题滚到滚动区顶部。"""
        scroll = getattr(self, "_scroll", None)
        wrap = getattr(self, "_full_wrap", None)
        if scroll is None or wrap is None:
            return
        target = wrap
        for bar in list(getattr(self, "_full_prompt_bars", None) or []):
            if bar is not None:
                target = bar
                break
        try:
            content = scroll.widget()
            vbar = scroll.verticalScrollBar()
            if content is None or vbar is None:
                raise RuntimeError("no scroll")
            scroll.ensureWidgetVisible(target, 0, 8)
            y = int(target.mapTo(content, QPoint(0, 0)).y())
            vbar.setValue(max(0, min(int(vbar.maximum()), y - 4)))
        except Exception:
            try:
                scroll.ensureWidgetVisible(wrap, 0, 24)
            except Exception:
                pass

    def _make_translate_lang_chk(self, lang: str) -> QCheckBox:
        """紧挨复制钮前的语种翻译开关。lang=en→翻译英文；zh→翻译中文。"""
        lang = "en" if lang == "en" else "zh"
        tip = (
            "翻译英文：打开后加载 opus-MT，仅更新英文完整提示词栏（中→英）；仅当前会话，不写入配置。"
            if lang == "en"
            else "翻译中文：打开后加载 opus-MT，仅更新中文完整提示词栏（英→中）；仅当前会话，不写入配置。"
        )
        chk = QCheckBox("")  # 仅开关，文案放 tooltip
        chk.setObjectName(
            "PromptTranslateEn" if lang == "en" else "PromptTranslateZh"
        )
        chk.setFocusPolicy(Qt.NoFocus)
        chk.setCursor(Qt.PointingHandCursor)
        chk.setFixedHeight(_COPY_H)
        chk.setToolTip(tip)
        if lang == "en":
            self.chk_translate_en = chk
            # clicked(bool) 在嵌入复合按钮时比 toggled 更稳（避免样式/同步吞掉）
            chk.clicked.connect(self._on_translate_en_toggled)
        else:
            self.chk_translate_zh = chk
            chk.clicked.connect(self._on_translate_zh_toggled)
        self._style_translate_lang_chk(chk)
        # 初始同步在两者都建好后由 _sync_translate_enable_chk 统一做
        return chk

    def _tr_check_mark_url(self) -> str:
        """勾选指示器白色对勾 SVG（QSS image:url，正斜杠）。"""
        import os
        import sys
        try:
            from utils.app_paths import resource_root
            base = resource_root()
        except Exception:
            base = getattr(sys, "_MEIPASS", None)
            if not base:
                base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, "assets", "check_mark_white.svg").replace("\\", "/")

    def _style_translate_lang_chk(self, chk: QCheckBox = None):
        """底部翻译开关：未选=描边空框；已选=强调色底 + 白色对勾（勿只填纯色块）。"""
        targets = []
        if chk is not None:
            targets = [chk]
        else:
            for attr in ("chk_translate_en", "chk_translate_zh"):
                w = getattr(self, attr, None)
                if w is not None:
                    targets.append(w)
        mark = self._tr_check_mark_url()
        for w in targets:
            oid = w.objectName() or "PromptTranslateLang"
            bd = tk("border")
            bg = tk("input_bg")
            acc = tk("accent")
            # 一旦写了 indicator 的 background/border，原生勾消失；
            # checked 必须显式 image 对勾，否则只剩一整块强调色。
            w.setStyleSheet(
                f"QCheckBox#{oid}{{"
                f"color:{tk('text')};background:transparent;border:none;"
                f"spacing:0;padding:0;margin:0;}}"
                f"QCheckBox#{oid}::indicator{{"
                f"width:15px;height:15px;border-radius:3px;"
                f"border:1px solid {bd};background:{bg};image:none;}}"
                f"QCheckBox#{oid}::indicator:hover{{"
                f"border-color:{acc};}}"
                f"QCheckBox#{oid}::indicator:checked{{"
                f"background:{acc};border-color:{acc};"
                f"image:url({mark});}}"
                f"QCheckBox#{oid}::indicator:checked:hover{{"
                f"background:{acc};border-color:{acc};"
                f"image:url({mark});}}"
            )

    def _style_translate_enable_chk(self):
        """restyle 兼容名 → 刷新两侧开关样式。"""
        self._style_translate_lang_chk(None)

    def _sync_translate_enable_chk(self):
        """同步两侧开关勾选态；任一侧开则启用模型列表。"""
        for lang, attr in (("en", "chk_translate_en"), ("zh", "chk_translate_zh")):
            chk = getattr(self, attr, None)
            if chk is None:
                continue
            on = self._translate_lang_is_enabled(lang)
            chk.blockSignals(True)
            try:
                chk.setChecked(on)
            finally:
                chk.blockSignals(False)
        combo = getattr(self, "cmb_translate_model", None)
        if combo is not None:
            models_ok = any(combo.itemData(i) for i in range(combo.count()))
            combo.setEnabled(bool(self._translate_is_enabled() and models_ok))

    def _on_translate_lang_toggled(self, lang: str, checked: bool):
        """仅当前会话生效，不写入 user.txt；只影响对应栏。"""
        lang = "en" if lang == "en" else "zh"
        on = bool(checked)
        self._set_host_translate_lang_enabled(lang, on)
        if self._translate_lang_is_enabled(lang) != on:
            self._set_host_translate_lang_enabled(lang, on)
        self._cancel_tr_worker()
        self._tr_req_id = int(getattr(self, "_tr_req_id", 0) or 0) + 1
        self._tr_status = "" if self._translate_is_enabled() else "off"
        self._tr_busy_lang = ""
        if on:
            # 打开时清掉该侧脏缓存（曾把中文源文当成英文译文）
            self._purge_invalid_tr_cache(lang)
        self._full_parts_sig_en = None
        self._full_parts_sig_zh = None
        self._sync_translate_enable_chk()
        self._update_tr_status_label()
        self._update_translate_switch_tips()
        self._refresh_full_prompt()
        if on:
            # 再踢一次，避免首帧 worker 未起
            QTimer.singleShot(0, self._refresh_full_prompt)

    def _on_translate_en_toggled(self, checked: bool):
        self._on_translate_lang_toggled("en", checked)

    def _on_translate_zh_toggled(self, checked: bool):
        self._on_translate_lang_toggled("zh", checked)

    def _make_translate_model_combo(self) -> QComboBox:
        combo = QComboBox()
        combo.setObjectName("PromptTranslateModel")
        combo.setFocusPolicy(Qt.NoFocus)
        combo.setFixedHeight(_COPY_H)
        combo.setMinimumWidth(120)
        combo.setMaximumWidth(150)
        combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        combo.setToolTip(
            "翻译模型：opus-MT 中英（本地双向）。需先打开「翻译英文」或「翻译中文」才会加载并翻译。"
        )
        self.cmb_translate_model = combo
        combo.currentIndexChanged.connect(self._on_translate_model_changed)
        self._style_translate_combo()
        self._reload_translate_model_combo()
        return combo

    def _style_translate_combo(self):
        combo = getattr(self, "cmb_translate_model", None)
        if combo is None:
            return
        combo.setStyleSheet(
            f"QComboBox#PromptTranslateModel{{"
            f"background:{tk('input_bg')};color:{tk('text')};"
            f"border:1px solid {tk('border')};border-radius:6px;"
            f"padding:0 8px;font-size:12px;}}"
            f"QComboBox#PromptTranslateModel::drop-down{{width:18px;border:none;}}"
            f"QComboBox#PromptTranslateModel QAbstractItemView{{"
            f"background:{tk('panel_2')};color:{tk('text')};"
            f"selection-background-color:{tk('accent')};}}"
        )

    def _reload_translate_model_combo(self):
        combo = getattr(self, "cmb_translate_model", None)
        if combo is None:
            return
        models = _list_opus_mt_models()
        want = self._host_translate_model()
        # 旧偏好可能是 opus-mt-zh-en / opus-mt-en-zh，统一迁到逻辑 id
        if want in ("opus-mt-zh-en", "opus-mt-en-zh", "opus-mt-zh_en", "opus-mt-en_zh"):
            want = _OPUS_MT_BUNDLE_ID
            self._set_host_translate_model(want)
        combo.blockSignals(True)
        try:
            combo.clear()
            if not models:
                combo.addItem("（无翻译模型）", "")
                combo.setEnabled(False)
            else:
                combo.setEnabled(True)
                for mid, label in models:
                    combo.addItem(label, mid)
                idx = 0
                for i in range(combo.count()):
                    if combo.itemData(i) == want:
                        idx = i
                        break
                combo.setCurrentIndex(idx)
                chosen = combo.currentData()
                if chosen and chosen != want:
                    self._set_host_translate_model(str(chosen))
                elif not want and chosen:
                    self._set_host_translate_model(str(chosen))
        finally:
            combo.blockSignals(False)
        self._sync_translate_enable_chk()

    def _on_translate_model_changed(self, _index: int):
        combo = getattr(self, "cmb_translate_model", None)
        if combo is None:
            return
        mid = combo.currentData()
        self._set_host_translate_model(str(mid or ""))
        self._cancel_tr_worker()
        self._tr_req_id = int(getattr(self, "_tr_req_id", 0) or 0) + 1
        self._tr_cache = {}
        self._tr_status = "" if self._translate_is_enabled() else "off"
        self._full_parts_sig_zh = None
        self._full_parts_sig_en = None
        self._refresh_full_prompt()
        self._schedule_persist()

    def _make_copy_all_btn(self, side: str) -> QPushButton:
        # 不用 apply_mini_button：全局 kind=mini 易限制宽度，中文左右被裁
        btn = QPushButton("复制全部")
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        btn.setFixedHeight(_COPY_H)
        btn.setStyleSheet(self._copy_all_qss())
        try:
            need = int(btn.fontMetrics().horizontalAdvance("复制全部") + 40)
        except Exception:
            need = 108
        btn.setFixedWidth(max(need, 100))
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
            f"padding:0 18px;border-radius:6px;"
            f"border:1px solid {tk('border')};"
            f"background:{tk('input_bg')};color:{tk('text')};"
            f"font-size:13px;font-weight:700;text-align:center;}}"
            f"QPushButton:hover{{"
            f"background:{h['hover_bg']};border-color:{h['hover_bd']};color:{h['hover_fg']};}}"
            f"QPushButton:pressed{{"
            f"background:{h['press_bg']};border-color:{h['press_bd']};color:{h['press_fg']};}}"
            f"QPushButton:disabled{{"
            f"background:{tk('accent_dis')};border-color:{tk('border')};"
            f"color:{tk('text_dim')};}}"
        )

    def _style_pick_btn(self, btn: QPushButton):
        self._style_field_square_btn(btn)
        kind = str(btn.property("pickKind") or "random")
        color = tk("text") if btn.isEnabled() else tk("text_dim")
        btn.setIcon(_pick_icon(kind, _HIST_ICON, color))
        btn.setText("")

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
        editing = bool(getattr(card, "_editing", False))
        locked = card._key in getattr(self, "_locked_keys", set())
        for attr, is_on, obj_name in (
            ("btn_lock", locked, "PromptOrderLock"),
            ("btn_freeze", frozen, "PromptOrderFreeze"),
        ):
            btn = getattr(card, attr, None)
            if btn is None:
                continue
            btn.setVisible(not editing)
            if attr == "btn_lock":
                if locked:
                    if theme.is_dark:
                        bbd, bbg, ico = "#f59e0b", "#78350f", "#fde68a"
                    else:
                        bbd, bbg, ico = "#d97706", "#fffbeb", "#b45309"
                    hov = self._hot_colors()
                    hover_bg, hover_bd = hov["hover_bg"], hov["hover_bd"]
                    press_bg, press_bd = hov["press_bg"], hov["press_bd"]
                else:
                    bbd, bbg, ico = bd, bg, fg
                    hover_bg = _mix_hex(bg, fg, 0.16)
                    hover_bd = fg
                    press_bg = _mix_hex(bg, fg, 0.28)
                    press_bd = fg
                tip = "锁定" if not locked else "解锁"
            else:
                if frozen:
                    hov = self._hot_colors()
                    bbd, bbg, ico = "#e4e4e7", "#1a1a1a", "#f8fafc"
                    hover_bg, hover_bd = hov["hover_bg"], hov["hover_bd"]
                    press_bg, press_bd = hov["press_bg"], hov["press_bd"]
                else:
                    bbd, bbg, ico = bd, bg, fg
                    hover_bg = _mix_hex(bg, fg, 0.16)
                    hover_bd = fg
                    press_bg = _mix_hex(bg, fg, 0.28)
                    press_bd = fg
                tip = ""
            inner = _TITLE_ACT - 2
            btn.setStyleSheet(
                f"QPushButton#{obj_name}{{"
                f"background:{bbg};border:1px solid {bbd};border-radius:4px;"
                f"padding:0;margin:0;"
                f"min-width:{inner}px;max-width:{inner}px;"
                f"min-height:{inner}px;max-height:{inner}px;}}"
                f"QPushButton#{obj_name}:hover{{"
                f"background:{hover_bg};border-color:{hover_bd};}}"
                f"QPushButton#{obj_name}:pressed{{"
                f"background:{press_bg};border-color:{press_bd};}}"
            )
            mark = getattr(btn, "set_mark", None)
            if callable(mark):
                btn.setIcon(QIcon())
                mark(is_on, ico)
            elif attr == "btn_freeze":
                btn.setIcon(_freeze_icon(frozen, _PASTE_ICON, ico))
            btn.setToolTip(tip)
        place = getattr(card, "_place_card_actions", None) or getattr(card, "_place_freeze", None)
        if callable(place):
            place()
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
        if key in getattr(self, "_frozen_keys", set()):
            self._sync_freeze_bar_layout(key, int(bar.height() or 0) or self._frozen_row_h(bar))

    def _style_one_edit(self, edit: QPlainTextEdit, tint: str):
        key = str(edit.property("promptKey") or "")
        empty = not (edit.toPlainText() or "").strip()
        if _is_full_prompt_key(key):
            # 左蓝右绿；相对「完整提示词」标题条 #202020 再压暗
            use_tint = "green" if (key == "full_en" or tint == "green") else "blue"
            bg = _full_col_bg(use_tint, empty)
            # 有正文时不在样式里写死 color，让各段 QTextCharFormat 分类色生效
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
        color_css = f"color:{fg};" if not (_is_full_prompt_key(key) and not empty) else ""
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
        try:
            pal.setColor(QPalette.PlaceholderText, ph)
        except Exception:
            pass
        if not (_is_full_prompt_key(key) and not empty):
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
        self._style_jump_btn()
        zh = getattr(self, "_full_prompt_zh", None)
        en = getattr(self, "_full_prompt_en", None)
        if zh is not None:
            self._style_one_edit(zh, "blue")
        if en is not None:
            self._style_one_edit(en, "green")
        self._style_full_title_bar()
        self._refresh_full_prompt()
        self._update_tr_status_label()
        self._style_translate_combo()
        self._style_translate_enable_chk()
        self._sync_translate_enable_chk()
        for attr in ("btn_undo", "btn_redo"):
            btn = getattr(self, attr, None)
            if btn is not None:
                self._style_hist_btn(btn)
        count = getattr(self, "lbl_hist_count", None)
        if count is not None:
            count.setStyleSheet(self._hist_count_qss())
        for attr in ("btn_field_add", "btn_field_rename", "btn_field_del",
                     "btn_pick_random", "btn_pick_seq"):
            btn = getattr(self, attr, None)
            if btn is not None:
                self._style_field_square_btn(btn)
                if attr in ("btn_pick_random", "btn_pick_seq"):
                    self._style_pick_btn(btn)
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
        tool_btns = list(self._add_btns)
        for btn in tool_btns:
            try:
                btn.objectName()
            except RuntimeError:
                continue
            self._style_add_preset_btn(btn)
        self._refresh_preset_paste_btns()
        for key in self._spec_keys():
            self._place_freeze_veil(key)
            self._place_lock_veil(key)
        ghost = getattr(self, "_drag_ghost", None)
        if ghost is not None:
            self._style_order_ghost(ghost)
        self._refresh_hist_btns()
        self._fit_all_edits()

    def on_enter(self):
        self._refresh_preset_paste_btns()
        self._sync_h3_all()
        for key in self._spec_keys():
            if key in getattr(self, "_locked_keys", set()):
                self._apply_lock_visual(key)
            else:
                self._place_lock_veil(key)
        self._sync_translate_enable_chk()
        self._reload_translate_model_combo()
        self._update_tr_status_label()
        self._refresh_full_prompt()
        QTimer.singleShot(0, self._fit_all_edits)
        QTimer.singleShot(0, self._sync_foot_align)
        QTimer.singleShot(0, self._sync_full_prompt_layout)

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
        rename = getattr(self, "btn_field_rename", None)
        if rename is not None:
            rename.setEnabled(bool(self._selected_key))

    def _select_order_card(self, key: str):
        prev = self._selected_key
        if prev == key:
            # Second click on already-selected card: scroll content to that category.
            self._scroll_after_h3_change(key)
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
            getattr(self, "btn_field_rename", None),
            getattr(self, "btn_field_del", None),
            getattr(self, "btn_pick_random", None),
            getattr(self, "btn_pick_seq", None),
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

        def _keep_other(btn) -> bool:
            try:
                return str(btn.property("presetKey") or "") != key
            except RuntimeError:
                return False

        self._add_btns = [b for b in self._add_btns if _keep_other(b)]
        self._chip_groups.pop(key, None)
        self._custom_presets.pop(key, None)
        self._freeze_btns.pop(key, None)
        self._freeze_veils.pop(key, None)
        self._lock_veils.pop(key, None)
        self._frozen_keys.discard(key)
        self._locked_keys.discard(key)
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
        self._lock_veils = {}
        self._xfer_btns = []
        self._paste_btns = []
        self._specs = list(specs) if specs else list(_FIELDS)
        for key, title, ph in self._specs:
            self._add_section_widget(key, title, ph)
        if self._selected_key not in set(self._spec_keys()):
            self._selected_key = None
        self._rebuild_order_cards()

    def _move_field(self, key: str, direction: str):
        self._note_category_activity(key)
        src = self._left[key] if direction == "to_right" else self._right[key]
        dst = self._right[key] if direction == "to_right" else self._left[key]
        text = src.toPlainText()
        if not (text or "").strip():
            return
        dst.setPlainText(text)
        src.setPlainText("")

    def _paste_field(self, side: str, key: str):
        self._note_category_activity(key)
        if key in getattr(self, "_frozen_keys", set()) or key in getattr(self, "_locked_keys", set()):
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
        # 兼容：orig→中栏，其它→英栏
        if side == "orig":
            self._copy_full_zh()
        else:
            self._copy_full_en()

    def _snapshot(self) -> dict:
        return {
            "orig": self._side_values("orig"),
            "new": self._side_values("new"),
            "field_order": self._spec_keys(),
            "custom_fields": self._export_custom(),
            "field_titles": {k: t for k, t, _p in self._specs},
            "frozen_fields": sorted(self._frozen_keys),
            "locked_fields": sorted(self._locked_keys),
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
            self._locked_keys = self._parse_key_set(
                snap.get("locked_fields") if isinstance(snap, dict) else None
            )
            for k in self._spec_keys():
                self._apply_freeze_visual(k)
                self._apply_lock_visual(k)
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
            "locked_fields": sorted(self._locked_keys),
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
        self._locked_keys = self._parse_key_set(data.get("locked_fields"))
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
            self._apply_lock_visual(key)
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

    def _frozen_row_h(self, _bar: QFrame) -> int:
        """冻结后只留一行：色条上下等距包住冻结图标，竖排分类名不占高。"""
        return 3 + _TITLE_ACT + 3

    def _sync_freeze_bar_layout(self, key: str, row_h: int):
        """冻结时把图标在色条里水平垂直居中；解冻恢复顶对齐的竖排标题。"""
        bar = self._title_bar_by_key.get(key)
        btn = self._freeze_btns.get(key)
        lab = self._title_labs.get(key)
        if bar is None or btn is None:
            return
        lay = bar.layout()
        if lay is None or lay.count() < 1:
            return
        frozen = key in getattr(self, "_frozen_keys", set())
        if frozen:
            # 布局会把图标挤向右下。冻住后改由几何居中，不交给布局排。
            if lay.indexOf(btn) >= 0:
                lay.removeWidget(btn)
            btn.setParent(bar)
            side = _TITLE_ACT
            bw = int(bar.width() or 0)
            bh = int(bar.height() or 0) or int(row_h)
            if bw > 0 and bh > 0:
                x = (bw - side) // 2
                y = (bh - side) // 2
                btn.setGeometry(x, y, side, side)
            btn.show()
            btn.raise_()
            return
        if lay.indexOf(btn) < 0:
            lay.insertWidget(0, btn, 0, Qt.AlignHCenter | Qt.AlignTop)
        stretch_index = lay.count() - 1
        left = right = _TITLE_BAR_PAD_H
        top = bot = 3
        m = lay.contentsMargins()
        if (m.left(), m.top(), m.right(), m.bottom()) != (left, top, right, bot):
            lay.setContentsMargins(left, top, right, bot)
        if lay.spacing() != _TITLE_COL_GAP:
            lay.setSpacing(_TITLE_COL_GAP)
        if stretch_index >= 0 and lay.stretch(stretch_index) != 1:
            lay.setStretch(stretch_index, 1)
        lay.setAlignment(btn, Qt.AlignHCenter | Qt.AlignTop)
        if lab is not None:
            lay.setAlignment(lab, Qt.AlignHCenter | Qt.AlignTop)

    def _sync_section_row(self, key: str):
        """竖排标题与左栏同高；右栏空位跟高。冻结时收成一行。"""
        if getattr(self, "_syncing_row", False):
            return
        edit = self._left.get(key)
        bar = self._title_bar_by_key.get(key)
        if edit is None or bar is None:
            return
        self._syncing_row = True
        try:
            lab = self._title_labs.get(key)
            wrap = self._sections.get(key)
            slot = self._right_slots.get(key)
            frozen = key in getattr(self, "_frozen_keys", set())
            if frozen:
                if lab is not None and not lab.isHidden():
                    lab.hide()
                h = self._frozen_row_h(bar)
            else:
                if lab is not None and lab.isHidden():
                    lab.show()
                title_h = self._title_natural_h(lab) if lab is not None else 0
                h = max(int(edit.needed_height()), int(title_h))
                flow = self._preset_flows.get(key)
                pane = self._preset_panes.get(key)
                if flow is not None:
                    pw = 0
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
            if slot is not None:
                if frozen:
                    slot.setFixedHeight(h)
                else:
                    slot.setMinimumHeight(h)
                    slot.setMaximumHeight(16777215)
            if wrap is not None:
                if frozen:
                    wrap.setFixedHeight(h)
                    if wrap.width() > 0 and wrap.height() != h:
                        wrap.resize(wrap.width(), h)
                elif wrap.maximumHeight() < 16777215:
                    wrap.setMinimumHeight(0)
                    wrap.setMaximumHeight(16777215)
            self._sync_freeze_bar_layout(key, h)
            self._place_freeze_veil(key)
            self._place_lock_veil(key)
        finally:
            self._syncing_row = False

    def _fit_all_edits(self):
        for key in list(self._left.keys()):
            self._apply_section_widths(key)
            self._sync_section_row(key)
        # 立即刷新一次（跳过输入防抖），内部已 sync 高度
        timer = getattr(self, "_full_prompt_timer", None)
        if timer is not None:
            timer.stop()
        self._refresh_full_prompt()

    def _schedule_persist(self):
        if self._loading:
            return
        self._save_timer.start()
        timer = getattr(self, "_full_prompt_timer", None)
        if timer is not None:
            timer.start()

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
        # 各字段 H3 预设是否展开（各分页共用）；条目顺序以 提示词分类.txt 为准
        self._h3_expanded: Dict[str, bool] = {}
        self._tab_rename_index = -1
        self._tab_filter_on = False
        # 完整提示词翻译模型（双栏常显，无 full_lang 切换）
        self._translate_model = ""
        # 翻译分开关：默认关且不落盘，避免进页/启动就加载 opus-MT
        self._translate_enabled = False  # 兼容：任一侧开则为 True
        self._translate_en_enabled = False
        self._translate_zh_enabled = False
        self._last_applied_pages = []  # guard against empty export after failed restore

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
        # If UI has no tabs (failed restore) or only empty shells, prefer last
        # successfully-parsed store pages so we do not wipe user 分页 on exit.
        live_has_body = any(_prompt_dict_has_body(p) for p in pages)
        remembered = getattr(self, "_last_applied_pages", None) or []
        remembered_has_body = any(
            isinstance(p, dict) and _prompt_dict_has_body(p) for p in remembered
        )
        if (not pages) or ((not live_has_body) and remembered_has_body):
            pages = []
            for item in remembered:
                if not isinstance(item, dict):
                    continue
                pid = _clean_page_id(item.get("id"), "") or _fresh_page_id(
                    {p.get("id") for p in pages}
                )
                title = _clean_page_title(item.get("title"), f"分类{len(pages) + 1}")
                page = _blank_prompt_page(pid, title)
                page.update(_copy_page_settings(item))
                page["id"] = pid
                page["title"] = title
                page.pop("custom_presets", None)
                pages.append(page)
            if pages and not active:
                active = pages[0]["id"]
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
            # 预设 chip 顺序只存在 docs/提示词分类.txt，不再写 preset_mru
            "translate_model": str(getattr(self, "_translate_model", "") or ""),
            # translate_enabled 不落盘：每次启动/进页默认关，加快加载
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
        # full_lang 已废弃（双栏常显）；忽略旧配置
        mid = str(data.get("translate_model") or "").strip()
        if mid in ("opus-mt-zh-en", "opus-mt-en-zh", "opus-mt-zh_en", "opus-mt-en_zh"):
            mid = _OPUS_MT_BUNDLE_ID
        if not mid and _opus_mt_bundle_ready():
            mid = _OPUS_MT_BUNDLE_ID
        self._translate_model = mid
        # 翻译开关不读 user.txt、不恢复：每次进页/启动一律关（会话内可再打开）
        self._translate_enabled = False
        self._translate_en_enabled = False
        self._translate_zh_enabled = False
        # 旧版 user.txt 里的 preset_mru 忽略：顺序以 提示词分类.txt 为准
        self._loading = True
        self.tabs.blockSignals(True)
        # Remember incoming pages so a failed sheet build cannot export an empty wipe.
        self._last_applied_pages = [dict(x) for x in pages if isinstance(x, dict)]
        try:
            self._discard_sheets()
            for i, item in enumerate(pages):
                title = item.get("title") or "默认"
                sheet = _PromptSheet()
                sheet._page_id = item.get("id") or _fresh_page_id(self._used_ids())
                sheet._preset_host = self
                sheet._h3_expanded = self._h3_expanded
                try:
                    sheet.apply_settings(
                        item,
                        char_data if (i == 0 and use_char) else None,
                        migrate_legacy_file=bool(migrate_file and i == 0),
                    )
                    if i == 0:
                        self._shared_presets = _merge_preset_maps(
                            self._shared_presets, sheet._custom_presets
                        )
                except Exception:
                    log.exception("提示词分页「%s」恢复失败，仍挂上该页以免整页空白", title)
                self._mount_sheet(sheet, title)
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
