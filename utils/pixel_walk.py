# utils/pixel_walk.py
# 复古像素四向行走表。纯 Pillow，不进 Qt，也不进现有的模型进程。
#
# 默认对齐全 RPG Maker MV/MZ 单角色表：
#   列：左步、站立、右步（播放顺序是 站立 → 左步 → 站立 → 右步）
#   行：正、左、右、后
# 界面四个画板的排列是 正、左、后、右。四帧开关打开时改为四列循环：左步、经过、右步、经过。

from __future__ import annotations

import os
from typing import Dict, List, Optional

from PIL import Image

DIRS = ("down", "left", "right", "up")
DIR_LABEL = {"down": "正", "left": "左", "right": "右", "up": "后"}
CELLS = (16, 24, 32, 48, 64)
CELL_MIN = 8
CELL_MAX = 512
COLOR_CHOICES = (0, 16, 32)
_BAD_NAME = '<>:"/\\|?*'


def snap_cell(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = 48
    # 允许自定义边长（正方形），只夹到 CELL_MIN..CELL_MAX
    return max(CELL_MIN, min(CELL_MAX, n))


def cell_wh(value) -> tuple:
    """格子尺寸统一成 (宽, 高)。整数 = 正方形；(w, h) 各自夹到 CELL_MIN..CELL_MAX。"""
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        return (snap_cell(value[0]), snap_cell(value[1]))
    n = snap_cell(value)
    return (n, n)


def snap_colors(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = 0
    return n if n in COLOR_CHOICES else 0


def play_order(nframes: int) -> tuple:
    """引擎真正播放的帧下标。三帧是乒乓球，四帧按列循环。"""
    if int(nframes) >= 4:
        return (0, 1, 2, 3)
    return (1, 0, 1, 2)


def load_image(path: str) -> Image.Image:
    im = Image.open(path)
    try:
        im.seek(0)
    except EOFError:
        pass
    try:
        from PIL import ImageOps
        im = ImageOps.exif_transpose(im)   # 照片带 EXIF 方向时按看到的方向放
    except Exception:
        pass
    if im.mode in ("I;16", "I;16B", "I;16L", "I;16N", "I"):
        # 16 位灰度直接转 RGBA 会被截成全白，先缩到 8 位
        wide = im.mode != "I" or (im.getextrema()[1] or 0) > 255
        im = (im.point(lambda v: v / 256.0) if wide else im).convert("L")
    return im.convert("RGBA")


def has_transparency(im: Image.Image) -> bool:
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    lo, _hi = im.getchannel("A").getextrema()
    return int(lo) < 128


def crop_opaque(im: Image.Image) -> Image.Image:
    rgba = im.convert("RGBA")
    alpha = rgba.getchannel("A")
    mask = alpha.point(lambda v: 255 if v > 0 else 0)   # 半透明也算内容
    box = mask.getbbox()
    if not box:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    return rgba.crop(box)


def harden_alpha(im: Image.Image) -> Image.Image:
    rgba = im.convert("RGBA")
    r, g, b, a = rgba.split()
    a = a.point(lambda v: 255 if v >= 128 else 0)
    return Image.merge("RGBA", (r, g, b, a))


def quantize_rgba(im: Image.Image, colors: int) -> Image.Image:
    """收到指定色数。0 表示保持原色。只减 RGB，alpha（含半透明）原样保留。"""
    im = im.convert("RGBA")
    n = snap_colors(colors)
    if n <= 0:
        return im
    alpha = im.getchannel("A")
    # 已经够少就不动颜色，避免再量化把色相挤偏
    counted = im.getcolors(maxcolors=n * n + 8)
    if counted is not None:
        opaque = {item[1][:3] for item in counted if item[1][3] > 0}
        if len(opaque) <= n:
            return im
    rgb = Image.new("RGB", im.size, (0, 0, 0))
    rgb.paste(im.convert("RGB"), mask=alpha.point(lambda v: 255 if v > 0 else 0))
    reduced = rgb.quantize(colors=n, method=Image.Quantize.MEDIANCUT).convert("RGBA")
    reduced.putalpha(alpha)
    return reduced


def fit_cell(im: Image.Image, cell: int) -> Image.Image:
    """最近邻放进格子。
    - 源图正好等于格子：原样返回（RGBA 副本）。
    - 源图不大于格子：用整张图（含透明边，保留原位置关系），能整倍放大就放大，脚贴底、水平居中。
    - 源图大于格子：裁掉透明边后按比例缩小，脚贴底、水平居中（旧逻辑）。
    """
    cw, ch = cell_wh(cell)
    rgba = im.convert("RGBA")
    if rgba.size == (cw, ch):
        return rgba.copy()
    if rgba.getbbox() is None:
        return Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    w, h = rgba.size
    if w <= cw and h <= ch:
        src = rgba
        scale = max(1, min(cw // w, ch // h))
        if scale > 1:
            src = src.resize((w * scale, h * scale), Image.Resampling.NEAREST)
    else:
        src = crop_opaque(rgba)
        w, h = src.size
        if w <= 0 or h <= 0:
            return Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        if w <= cw and h <= ch:
            pass   # 裁边后已能放下：不缩放
        else:
            scale = min(cw / float(w), ch / float(h))
            nw = min(cw, max(1, int(round(w * scale))))
            nh = min(ch, max(1, int(round(h * scale))))
            src = src.resize((nw, nh), Image.Resampling.NEAREST)
    canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    x = (cw - src.size[0]) // 2
    y = ch - src.size[1]
    canvas.paste(src, (x, y))   # 目标全透明且不重叠：直接贴，带 mask 会把半透明再乘一次
    return canvas


def fit_note(im: Image.Image, cell) -> tuple:
    """导入时 fit_cell 会怎么改尺寸：返回 (提示文字, 是否警告)，不改尺寸返回 ("", False)。"""
    cw, ch = cell_wh(cell)
    rgba = im.convert("RGBA")
    w, h = rgba.size
    if (w, h) == (cw, ch) or rgba.getbbox() is None:
        return "", False
    if w <= cw and h <= ch:
        scale = max(1, min(cw // w, ch // h))
        return (f"已放大 {scale} 倍", False) if scale > 1 else ("", False)
    sw, sh = crop_opaque(rgba).size
    if sw <= cw and sh <= ch:
        return "", False
    k = min(cw / float(sw), ch / float(sh))
    nw = min(cw, max(1, int(round(sw * k))))
    nh = min(ch, max(1, int(round(sh * k))))
    return f"原图 {w}×{h} 缩小到 {nw}×{nh}，细节可能丢失", True


def frame_count(path: str) -> int:
    try:
        with Image.open(path) as im:
            return int(getattr(im, "n_frames", 1) or 1)
    except Exception:
        return 1


def recanvas(im: Image.Image, cell) -> tuple:
    """改格子不拉伸：扩大四周补透明、缩小裁掉；水平居中、垂直贴底（和导入时脚贴底一致）。
    返回 (新图, 被裁掉的有内容像素数)。"""
    cw, ch = cell_wh(cell)
    rgba = im.convert("RGBA")
    w, h = rgba.size
    if (w, h) == (cw, ch):
        return rgba.copy(), 0
    ox = (cw - w) // 2
    oy = ch - h
    out = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    out.paste(rgba, (ox, oy))
    before = sum(rgba.getchannel("A").point(lambda v: 1 if v > 0 else 0).getdata())
    after = sum(out.getchannel("A").point(lambda v: 1 if v > 0 else 0).getdata())
    return out, int(before - after)


def prepare_cell(im: Image.Image, cell: int, colors: int) -> Image.Image:
    return quantize_rgba(fit_cell(im, cell), colors)


def make_step(standing: Image.Image, leg_ratio: float, dx: int, lift: int) -> Image.Image:
    """站立图拆成上身和腿。迈步帧上身抬 1 像素，腿左右错开，脚仍贴底。"""
    im = standing.convert("RGBA")
    w, h = im.size
    dx = int(dx)
    lift = max(0, int(lift))
    if dx == 0 and lift == 0:
        return im.copy()
    ratio = max(0.2, min(0.7, float(leg_ratio)))
    leg_h = max(1, min(h - 1, int(round(h * ratio))))
    cut = h - leg_h
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    upper = im.crop((0, 0, w, cut))
    legs = im.crop((0, cut, w, h))
    if upper.size[1] > 0:
        out.paste(upper, (0, -lift), upper)
        if lift > 0:
            seam = upper.crop((0, upper.size[1] - 1, w, upper.size[1]))
            for gy in range(max(0, cut - lift), cut):
                out.paste(seam, (0, gy), seam)
    if legs.size[1] > 0:
        out.paste(legs, (dx, cut), legs)
    return out


def build_frames(standing: Optional[Image.Image], nframes: int, leg_ratio: float, stride: int) -> List[Optional[Image.Image]]:
    n = 4 if int(nframes) >= 4 else 3
    if standing is None:
        return [None] * n
    stride = max(0, min(3, int(stride)))
    lift = 1 if stride else 0
    stand = standing.copy()
    left = make_step(stand, leg_ratio, -stride, lift)
    right = make_step(stand, leg_ratio, stride, lift)
    if n == 4:
        return [left, stand.copy(), right, stand.copy()]
    return [left, stand.copy(), right]


def frame_has_pixels(im: Optional[Image.Image]) -> bool:
    if im is None:
        return False
    _lo, hi = im.convert("RGBA").getchannel("A").getextrema()
    return int(hi) > 0


def compose_sheet(frames_by_dir: Dict[str, List[Optional[Image.Image]]], cell: int) -> Image.Image:
    """四行固定为正、左、右、后。空格留透明，表的行列数不变。"""
    cw, ch = cell_wh(cell)
    cols = 0
    for d in DIRS:
        cols = max(cols, len(frames_by_dir.get(d) or []))
    cols = max(1, cols)
    sheet = Image.new("RGBA", (cols * cw, 4 * ch), (0, 0, 0, 0))
    for row, d in enumerate(DIRS):
        frames = frames_by_dir.get(d) or []
        for col, fr in enumerate(frames):
            if fr is None:
                continue
            piece = fr.convert("RGBA")
            if piece.size != (cw, ch):
                piece = prepare_cell(piece, (cw, ch), 0)
            sheet.paste(piece, (col * cw, row * ch))
    return sheet


def _to_gif_frame(im: Image.Image) -> Image.Image:
    rgba = im.convert("RGBA")
    alpha = rgba.getchannel("A")
    rgb = Image.new("RGB", rgba.size, (0, 0, 0))
    # 用二值遮罩：半透明像素保留原色，不被黑底压暗
    rgb.paste(rgba.convert("RGB"), mask=alpha.point(lambda v: 255 if v >= 128 else 0))
    pal = rgb.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    px = pal.load()
    a = alpha.load()
    w, h = pal.size
    for y in range(h):
        for x in range(w):
            if a[x, y] < 128:
                px[x, y] = 255
    raw = list(pal.getpalette() or [])
    raw = raw[: 255 * 3] + [0, 0, 0]
    if len(raw) < 768:
        raw.extend([0] * (768 - len(raw)))
    pal.putpalette(raw[:768])
    pal.info["transparency"] = 255
    return pal


def write_gif(frames: List[Image.Image], path: str, duration_ms: int) -> None:
    if not frames:
        raise ValueError("没有可写的帧")
    pal = [_to_gif_frame(fr) for fr in frames]
    ms = max(40, int(duration_ms))
    first, rest = pal[0], pal[1:]
    first.save(
        path,
        save_all=True,
        append_images=rest,
        duration=ms,
        loop=0,
        disposal=2,
        transparency=255,
        optimize=False,
    )


# 调色盘预设。经典是一套常用 16 色；GB 是掌机四绿；Pico-8 是公开的 16 色。
PALETTES = {
    "经典": (
        "#1a1c2c", "#5d275d", "#b13e53", "#ef7d57",
        "#ffcd75", "#a7f070", "#38b764", "#257179",
        "#29366f", "#3b5dc9", "#41a6f6", "#73eff7",
        "#f4f4f4", "#94b0c2", "#566c86", "#333c57",
    ),
    "GB": ("#0f380f", "#306230", "#8bac0f", "#9bbc0f"),
    "Pico-8": (
        "#000000", "#1d2b53", "#7e2553", "#008751",
        "#ab5236", "#5f574f", "#c2c3c7", "#fff1e8",
        "#ff004d", "#ffa300", "#ffec27", "#00e436",
        "#29adff", "#83769c", "#ff77a8", "#ffccaa",
    ),
}


def hex_rgb(text: str) -> tuple:
    s = (text or "").strip().lstrip("#")
    if len(s) != 6:
        return (0, 0, 0)
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def blank_cell(cell: int) -> Image.Image:
    return Image.new("RGBA", cell_wh(cell), (0, 0, 0, 0))


def resize_cell(im: Image.Image, cell: int) -> Image.Image:
    n = cell_wh(cell)
    rgba = im.convert("RGBA")
    if rgba.size == n:
        return rgba
    return rgba.resize(n, Image.Resampling.NEAREST)


def composite_layers(layers: List[dict], cell: int) -> Image.Image:
    """从下往上叠。最底下一层不吃透明度，上面的层按各自透明度叠上去。"""
    n = cell_wh(cell)
    out = Image.new("RGBA", n, (0, 0, 0, 0))
    for index, layer in enumerate(layers or []):
        if not layer.get("visible", True):
            continue
        src = layer.get("image")
        if src is None:
            continue
        piece = resize_cell(src, n)
        if index > 0:
            try:
                opacity = float(layer.get("opacity", 1))
            except (TypeError, ValueError):
                opacity = 1.0
            opacity = max(0.0, min(1.0, opacity))
            if opacity <= 0:
                continue
            if opacity < 0.999:
                r, g, b, a = piece.split()
                a = a.point(lambda v, f=opacity: int(round(v * f)))
                piece = Image.merge("RGBA", (r, g, b, a))
        out = Image.alpha_composite(out, piece)
    return out


def flood_fill(im: Image.Image, x: int, y: int, color: tuple) -> None:
    """把连成一片的同色像素换成 color。改的是这张图本身。"""
    rgba = im.convert("RGBA")
    if rgba is not im:
        im.paste(rgba)
    w, h = im.size
    if x < 0 or y < 0 or x >= w or y >= h:
        return
    src = im.getpixel((x, y))
    if src == color:
        return
    pix = im.load()
    stack = [(int(x), int(y))]
    seen = set()
    while stack:
        cx, cy = stack.pop()
        if (cx, cy) in seen or cx < 0 or cy < 0 or cx >= w or cy >= h:
            continue
        if pix[cx, cy] != src:
            continue
        seen.add((cx, cy))
        pix[cx, cy] = color
        stack.append((cx + 1, cy))
        stack.append((cx - 1, cy))
        stack.append((cx, cy + 1))
        stack.append((cx, cy - 1))


def next_free_path(folder: str, stem: str, ext: str = ".png") -> Optional[str]:
    """同名就从 _02 往后排（文件或文件夹都算占用）。ext 为空表示文件夹。没有空位时返回空。"""
    first = os.path.join(folder, stem + ext)
    if not os.path.exists(first):
        return first
    for n in range(2, 1000):
        path = os.path.join(folder, f"{stem}_{n:02d}{ext}")
        if not os.path.exists(path):
            return path
    return None


def next_png_path(folder: str, stem: str) -> Optional[str]:
    """同名就从 _02 往后排。没有空位时返回空。"""
    first = os.path.join(folder, stem + ".png")
    if not os.path.exists(first):
        return first
    for n in range(2, 100):
        path = os.path.join(folder, f"{stem}_{n:02d}.png")
        if not os.path.exists(path):
            return path
    return None


def compose_clip_sheet(rows: List[List[Image.Image]], cell: int) -> Image.Image:
    """每一行是一条动画，列就是 01、02、03。空帧留透明。"""
    n = cell_wh(cell)
    cw, ch = n
    cols = 1
    for row in rows:
        cols = max(cols, len(row))
    sheet = Image.new("RGBA", (cols * cw, max(1, len(rows)) * ch), (0, 0, 0, 0))
    for y, frames in enumerate(rows):
        for x, fr in enumerate(frames):
            if fr is None:
                continue
            piece = resize_cell(fr, n)
            sheet.paste(piece, (x * cw, y * ch))
    return sheet


def clean_stem(name: str, dollar: bool) -> str:
    stem = "".join(c for c in (name or "").strip() if c not in _BAD_NAME and ord(c) >= 32)
    stem = stem.rstrip(" .")
    if not stem:
        stem = "角色"
    if len(stem) > 80:
        stem = stem[:80].rstrip(" .") or "角色"
    if dollar and not stem.startswith("$"):
        stem = "$" + stem
    return stem


def _blank_slot() -> dict:
    return {"source": None, "locks": {}}


class WalkBoard:
    """四向站立图、锁定帧，以及由站立图推出来的迈步。"""

    def __init__(self):
        self.cell = 48
        self.colors = 0
        self.stride = 2
        self.leg = 0.42
        self.frames_n = 3
        self.slots = {d: _blank_slot() for d in DIRS}
        self.stand: Dict[str, Optional[Image.Image]] = {d: None for d in DIRS}
        self.frames: Dict[str, List[Optional[Image.Image]]] = {
            d: [None, None, None] for d in DIRS
        }

    def configure(self, *, cell=None, colors=None, stride=None, leg=None, frames_n=None) -> None:
        if cell is not None:
            self.cell = snap_cell(cell)
        if colors is not None:
            self.colors = snap_colors(colors)
        if stride is not None:
            try:
                self.stride = max(0, min(3, int(stride)))
            except (TypeError, ValueError):
                self.stride = 2
        if leg is not None:
            try:
                ratio = float(leg)
            except (TypeError, ValueError):
                ratio = 0.42
            if ratio > 1:
                ratio = ratio / 100.0
            self.leg = max(0.2, min(0.7, ratio))
        if frames_n is not None:
            self.frames_n = 4 if int(frames_n) >= 4 else 3
        self.rebuild()

    def set_source(self, direction: str, im: Image.Image) -> None:
        if direction not in self.slots:
            return
        self.slots[direction]["source"] = crop_opaque(im)
        self.slots[direction]["locks"].clear()
        self.rebuild()

    def set_lock(self, direction: str, index: int, im: Image.Image) -> None:
        if direction not in self.slots:
            return
        n = self.frames_n
        if index < 0 or index >= n:
            return
        self.slots[direction]["locks"][int(index)] = crop_opaque(im)
        self.rebuild()

    def clear_locks(self, direction: str) -> None:
        if direction not in self.slots:
            return
        self.slots[direction]["locks"].clear()
        self.rebuild()

    def mirror_right_to_left(self) -> bool:
        src = self.slots["right"]["source"]
        if src is None:
            return False
        self.slots["left"]["source"] = src.transpose(Image.FLIP_LEFT_RIGHT)
        self.slots["left"]["locks"].clear()
        self.rebuild()
        return True

    def rebuild(self) -> None:
        n = self.frames_n
        for d in DIRS:
            src = self.slots[d]["source"]
            standing = prepare_cell(src, self.cell, self.colors) if src is not None else None
            self.stand[d] = standing
            frames = build_frames(standing, n, self.leg, self.stride)
            for index, raw in list(self.slots[d]["locks"].items()):
                if raw is None or index < 0 or index >= n:
                    continue
                frames[index] = prepare_cell(raw, self.cell, self.colors)
            self.frames[d] = frames

    def sheet(self) -> Image.Image:
        return compose_sheet(self.frames, self.cell)

    def gif_frames(self, direction: str) -> List[Image.Image]:
        frames = self.frames.get(direction) or []
        out = []
        for index in play_order(self.frames_n):
            if index >= len(frames):
                continue
            fr = frames[index]
            if frame_has_pixels(fr):
                out.append(fr)
        return out

    def any_pixels(self) -> bool:
        for d in DIRS:
            for fr in self.frames.get(d) or []:
                if frame_has_pixels(fr):
                    return True
        return False


def self_check() -> None:
    """给开发时跑的行为检查。页面不依赖它。"""
    cell = 48
    src = Image.new("RGBA", (16, 32), (0, 0, 0, 0))
    for y in range(0, 16):
        for x in range(4, 12):
            src.putpixel((x, y), (200, 40, 40, 255))
    for y in range(16, 32):
        for x in range(3, 8):
            src.putpixel((x, y), (40, 80, 220, 255))
        for x in range(9, 14):
            src.putpixel((x, y), (40, 80, 220, 255))
    board = WalkBoard()
    board.configure(cell=cell, colors=0, stride=2, leg=0.5, frames_n=3)
    board.set_source("down", src)
    stand = board.frames["down"][1]
    assert stand is not None and stand.size == (cell, cell)
    assert stand.tobytes() == board.stand["down"].tobytes()
    assert board.frames["down"][0].tobytes() != stand.tobytes()
    assert board.frames["down"][2].tobytes() != stand.tobytes()
    assert board.frames["down"][0].tobytes() != board.frames["down"][2].tobytes()
    # 脚仍在底边
    bottom = [board.frames["down"][0].getpixel((x, cell - 1))[3] for x in range(cell)]
    assert any(a >= 128 for a in bottom)
    board.set_lock("down", 0, board.frames["down"][0])
    frozen = board.frames["down"][0].tobytes()
    board.configure(stride=3)
    assert board.frames["down"][0].tobytes() == frozen
    assert board.frames["down"][2].tobytes() != frozen
    right = Image.new("RGBA", (8, 4), (0, 0, 0, 0))
    right.putpixel((0, 1), (255, 0, 0, 255))
    right.putpixel((1, 1), (0, 255, 0, 255))
    board.set_source("right", right)
    assert board.mirror_right_to_left()
    flipped = board.slots["right"]["source"].transpose(Image.FLIP_LEFT_RIGHT)
    assert board.slots["left"]["source"].tobytes() == flipped.tobytes()
    sheet = board.sheet()
    assert sheet.size == (cell * 3, cell * 4)
    board.configure(frames_n=4)
    assert len(board.frames["down"]) == 4
    assert board.sheet().size == (cell * 4, cell * 4)
    assert play_order(3) == (1, 0, 1, 2)
    assert play_order(4) == (0, 1, 2, 3)
    assert clean_stem("a/b", True) == "$ab"
    assert clean_stem("", False) == "角色"
    assert clean_stem("$x", False) == "$x"
    many = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    for i in range(40):
        many.putpixel((i % 32, i % 32), ((i * 7) % 256, (i * 13) % 256, (i * 19) % 256, 255))
    reduced = quantize_rgba(many, 16)
    opaque = {px for _n, px in (reduced.getcolors() or []) if px[3] >= 128}
    assert 1 <= len(opaque) <= 16
    gif_path = os.path.join(os.environ.get("TEMP") or ".", "_pixel_walk_self.gif")
    write_gif(board.gif_frames("down"), gif_path, 125)
    with Image.open(gif_path) as gif:
        assert gif.format == "GIF"
        assert getattr(gif, "n_frames", 1) >= 2
        back = gif.convert("RGBA")
        assert back.getchannel("A").getextrema()[0] == 0
    try:
        os.remove(gif_path)
    except OSError:
        pass
    red = blank_cell(4)
    red.putpixel((1, 1), (255, 0, 0, 255))
    blue = blank_cell(4)
    blue.putpixel((1, 1), (0, 0, 255, 255))
    mixed = composite_layers(
        [
            {"image": red, "opacity": 1, "visible": True},
            {"image": blue, "opacity": 0.5, "visible": True},
        ],
        4,
    )
    px = mixed.getpixel((1, 1))
    assert px[0] > 80 and px[2] > 80 and px[3] == 255
    hidden = composite_layers(
        [
            {"image": red, "opacity": 1, "visible": True},
            {"image": blue, "opacity": 0.5, "visible": False},
        ],
        4,
    )
    assert hidden.getpixel((1, 1))[:3] == (255, 0, 0)
    blob = blank_cell(3)
    blob.putpixel((0, 0), (1, 2, 3, 255))
    blob.putpixel((1, 0), (1, 2, 3, 255))
    blob.putpixel((1, 1), (9, 9, 9, 255))
    flood_fill(blob, 0, 0, (8, 8, 8, 255))
    assert blob.getpixel((1, 0)) == (8, 8, 8, 255)
    assert blob.getpixel((1, 1)) == (9, 9, 9, 255)
    seq_dir = os.path.join(os.environ.get("TEMP") or ".", "_pixel_seq")
    os.makedirs(seq_dir, exist_ok=True)
    try:
        a = next_png_path(seq_dir, "甲")
        assert a and a.endswith("甲.png")
        open(a, "wb").close()
        b = next_png_path(seq_dir, "甲")
        assert b and b.endswith("甲_02.png")
    finally:
        import shutil
        shutil.rmtree(seq_dir, ignore_errors=True)


# ---------- 画板工具用的几何：直线 / 矩形 / 椭圆 / 整图平移 ----------

def line_points(x0: int, y0: int, x1: int, y1: int) -> List[tuple]:
    """Bresenham 直线，含两端点。"""
    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    pts = []
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        pts.append((x0, y0))
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy
    return pts


def rect_points(x0: int, y0: int, x1: int, y1: int, filled: bool = False) -> List[tuple]:
    """两角点围成的矩形，空心只要边。"""
    l, r = sorted((int(x0), int(x1)))
    t, b = sorted((int(y0), int(y1)))
    pts = []
    for y in range(t, b + 1):
        if filled or y in (t, b):
            pts.extend((x, y) for x in range(l, r + 1))
        else:
            pts.append((l, y))
            if r != l:
                pts.append((r, y))
    return pts


def ellipse_points(x0: int, y0: int, x1: int, y1: int, filled: bool = False) -> List[tuple]:
    """外接框里的椭圆。按行求左右边界，空心时补齐相邻行之间的缺口。"""
    l, r = sorted((int(x0), int(x1)))
    t, b = sorted((int(y0), int(y1)))
    if r - l < 2 or b - t < 2:
        return rect_points(l, t, r, b, True)
    cx = (l + r) / 2.0
    cy = (t + b) / 2.0
    rx = (r - l) / 2.0 + 0.5
    ry = (b - t) / 2.0 + 0.5
    spans = {}
    for y in range(t, b + 1):
        dy = (y - cy) / ry
        k = 1.0 - dy * dy
        if k < 0:
            continue
        half = rx * (k ** 0.5)
        xa = int(round(cx - half + 0.5))
        xb = int(round(cx + half - 0.5))
        xa = max(l, xa)
        xb = min(r, xb)
        if xa > xb:
            xa = xb = int(round(cx))
        spans[y] = (xa, xb)
    pts = set()
    ys = sorted(spans)
    for i, y in enumerate(ys):
        xa, xb = spans[y]
        if filled:
            pts.update((x, y) for x in range(xa, xb + 1))
            continue
        pts.add((xa, y))
        pts.add((xb, y))
        for ny in (ys[i - 1] if i > 0 else None, ys[i + 1] if i + 1 < len(ys) else None):
            if ny is None:
                continue
            na, nb = spans[ny]
            # 左边：本行到相邻行左端之间连起来
            for x in range(min(xa, na), max(xa, na)):
                if x >= xa:
                    pts.add((x, y))
            for x in range(min(xb, nb) + 1, max(xb, nb) + 1):
                if x <= xb:
                    pts.add((x, y))
        if y in (t, b):
            pts.update((x, y) for x in range(xa, xb + 1))
    return sorted(pts)


def shift_image(im: Image.Image, dx: int, dy: int) -> Image.Image:
    """整图循环平移：移出去的一边从另一边回来。返回新图。"""
    rgba = im.convert("RGBA")
    w, h = rgba.size
    dx %= max(1, w)
    dy %= max(1, h)
    if not dx and not dy:
        return rgba.copy()
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for ox in (dx - w, dx):
        for oy in (dy - h, dy):
            out.paste(rgba, (ox, oy))
    return out


def write_gif_timed(frames: List[Image.Image], path: str, durations: List[int]) -> None:
    """和 write_gif 一样，但每帧有自己的时长（毫秒）。"""
    if not frames:
        raise ValueError("没有可写的帧")
    pal = [_to_gif_frame(fr) for fr in frames]
    ms = [max(20, int(durations[i] if i < len(durations) else durations[-1])) for i in range(len(pal))]
    first, rest = pal[0], pal[1:]
    first.save(
        path,
        save_all=True,
        append_images=rest,
        duration=ms,
        loop=0,
        disposal=2,
        transparency=255,
        optimize=False,
    )


def tint_ghost(im: Image.Image, rgb: tuple) -> Image.Image:
    """洋葱皮用：保留形状（透明度），整张染成一个颜色。"""
    rgba = im.convert("RGBA")
    out = Image.new("RGBA", rgba.size, tuple(rgb[:3]) + (255,))
    out.putalpha(rgba.getchannel("A"))
    return out


def _hsv_hex(h: float, s: float, v: float) -> str:
    import colorsys
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return f"#{int(round(r * 255)):02x}{int(round(g * 255)):02x}{int(round(b * 255)):02x}"


def _default_64() -> tuple:
    """8 级灰 + 14 个色相 × 4 级明暗 = 64 色。"""
    out = [_hsv_hex(0, 0, i / 7.0) for i in range(8)]
    hues = [0, 15, 30, 45, 60, 90, 120, 160, 180, 200, 220, 250, 280, 320]
    for s_, v_ in ((0.85, 0.45), (0.8, 0.75), (0.7, 0.95), (0.35, 1.0)):
        for h in hues:
            out.append(_hsv_hex(h / 360.0, s_, v_))
    return tuple(out[:64])


def palette_64(name: str) -> tuple:
    """把预设补足 64 色：原色在前，再补每色的明暗阶梯和灰阶，去重。"""
    if name == "默认64" or name not in PALETTES:
        return _default_64()
    base = [c.lower() for c in PALETTES[name]]
    out = []

    def push(c):
        if c not in out and len(out) < 64:
            out.append(c)

    for c in base:
        push(c)
    import colorsys
    for factor in (0.55, 1.35, 0.3, 1.7, 0.8, 1.15):
        for c in base:
            r, g, b = hex_rgb(c)
            h, s_, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            if factor > 1:
                push(_hsv_hex(h, s_ / factor, min(1.0, v * factor)))
            else:
                push(_hsv_hex(h, s_, v * factor))
    for c in _default_64():
        push(c)
    return tuple(out)


# ---------- 第十一阶段：动效页多尺寸帧导入（1:1 不缩放，按对齐放入母板） ----------

ALIGNS = ("左上", "上中", "右上", "左中", "居中", "右中", "左下", "下中", "右下")   # 3×3，行优先
ALIGN_DEFAULT = "下中"
_ALIGN_OLD = {"底部居中": "下中", "左上角": "左上", "居中": "居中"}   # 第十一阶段旧值


def norm_align(value) -> str:
    v = str(value or "")
    v = _ALIGN_OLD.get(v, v)
    return v if v in ALIGNS else ALIGN_DEFAULT


def natural_key(path: str) -> tuple:
    """自然排序：数字段按数值比，anim_001_009 在 anim_001_010 前。"""
    import re
    name = os.path.basename(path).lower()
    return tuple((0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.split(r"(\d+)", name) if t)


def frame_group(path: str) -> str:
    """组名：去掉扩展名和最后一段 _数字（anim_000_003 → anim_000）；没有就用整个文件名。"""
    import re
    stem = os.path.splitext(os.path.basename(path))[0]
    m = re.match(r"^(.*?)[_\-\s]?(\d+)$", stem)
    return (m.group(1) or stem) if m else stem


def group_paths(paths: List[str]) -> List[tuple]:
    """自然排序后按组名分组，返回 [(组名, [路径…])]，组按首次出现的顺序。"""
    out: Dict[str, List[str]] = {}
    for p in sorted(paths, key=natural_key):
        out.setdefault(frame_group(p), []).append(p)
    return list(out.items())


def _opaque(im: Image.Image) -> int:
    return sum(1 for v in im.getchannel("A").getdata() if v > 0)


def align_offset(size, cell, align: str) -> tuple:
    w, h = size
    cw, ch = cell_wh(cell)
    i = ALIGNS.index(norm_align(align))
    col, row = i % 3, i // 3
    ox = (0, (cw - w) // 2, cw - w)[col]
    oy = (0, (ch - h) // 2, ch - h)[row]
    return ox, oy


def place_image(im: Image.Image, cell, align: str = ALIGN_DEFAULT) -> tuple:
    """动效页专用：1:1 不缩放放进母板，超出裁掉、不足补透明。返回 (新图, 被裁掉的有内容像素数)。"""
    rgba = im.convert("RGBA")
    cw, ch = cell_wh(cell)
    if rgba.size == (cw, ch):
        return rgba.copy(), 0
    out = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    out.paste(rgba, align_offset(rgba.size, (cw, ch), align))
    return out, max(0, _opaque(rgba) - _opaque(out))
