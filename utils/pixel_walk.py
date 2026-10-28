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
CELLS = (16, 32, 48, 64)
COLOR_CHOICES = (0, 16, 32)
_BAD_NAME = '<>:"/\\|?*'


def snap_cell(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = 48
    return min(CELLS, key=lambda c: abs(c - n))


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
    return im.convert("RGBA")


def has_transparency(im: Image.Image) -> bool:
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    lo, _hi = im.getchannel("A").getextrema()
    return int(lo) < 128


def crop_opaque(im: Image.Image) -> Image.Image:
    rgba = im.convert("RGBA")
    alpha = rgba.getchannel("A")
    mask = alpha.point(lambda v: 255 if v >= 128 else 0)
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
    """收到指定色数。0 表示保持原色，只把半透明收成实心或全透。"""
    im = harden_alpha(im)
    n = snap_colors(colors)
    if n <= 0:
        return im
    alpha = im.getchannel("A")
    # 已经够少就不动颜色，避免再量化把色相挤偏
    counted = im.getcolors(maxcolors=n * n + 8)
    if counted is not None:
        opaque = [item for item in counted if item[1][3] >= 128]
        if len(opaque) <= n:
            return im
    rgb = Image.new("RGB", im.size, (0, 0, 0))
    rgb.paste(im.convert("RGB"), mask=alpha)
    reduced = rgb.quantize(colors=n, method=Image.Quantize.MEDIANCUT).convert("RGBA")
    reduced.putalpha(alpha)
    return reduced


def fit_cell(im: Image.Image, cell: int) -> Image.Image:
    """最近邻放进格子，脚贴底边，水平居中。能整倍放大时才放大。"""
    cell = snap_cell(cell)
    src = crop_opaque(im)
    w, h = src.size
    if w <= 0 or h <= 0:
        return Image.new("RGBA", (cell, cell), (0, 0, 0, 0))
    if w <= cell and h <= cell:
        scale = max(1, min(cell // w, cell // h))
        if scale > 1:
            src = src.resize((w * scale, h * scale), Image.Resampling.NEAREST)
    else:
        scale = min(cell / float(w), cell / float(h))
        nw = max(1, int(round(w * scale)))
        nh = max(1, int(round(h * scale)))
        nw = min(cell, nw)
        nh = min(cell, nh)
        src = src.resize((nw, nh), Image.Resampling.NEAREST)
    canvas = Image.new("RGBA", (cell, cell), (0, 0, 0, 0))
    x = (cell - src.size[0]) // 2
    y = cell - src.size[1]
    canvas.paste(src, (x, y), src)
    return canvas


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
    return int(hi) >= 128


def compose_sheet(frames_by_dir: Dict[str, List[Optional[Image.Image]]], cell: int) -> Image.Image:
    """四行固定为正、左、右、后。空格留透明，表的行列数不变。"""
    cell = snap_cell(cell)
    cols = 0
    for d in DIRS:
        cols = max(cols, len(frames_by_dir.get(d) or []))
    cols = max(1, cols)
    sheet = Image.new("RGBA", (cols * cell, 4 * cell), (0, 0, 0, 0))
    for row, d in enumerate(DIRS):
        frames = frames_by_dir.get(d) or []
        for col, fr in enumerate(frames):
            if fr is None:
                continue
            piece = fr.convert("RGBA")
            if piece.size != (cell, cell):
                piece = prepare_cell(piece, cell, 0)
            sheet.paste(piece, (col * cell, row * cell), piece)
    return sheet


def _to_gif_frame(im: Image.Image) -> Image.Image:
    rgba = im.convert("RGBA")
    alpha = rgba.getchannel("A")
    rgb = Image.new("RGB", rgba.size, (0, 0, 0))
    rgb.paste(rgba.convert("RGB"), mask=alpha)
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
    n = snap_cell(cell)
    return Image.new("RGBA", (n, n), (0, 0, 0, 0))


def resize_cell(im: Image.Image, cell: int) -> Image.Image:
    n = snap_cell(cell)
    rgba = im.convert("RGBA")
    if rgba.size == (n, n):
        return rgba
    return rgba.resize((n, n), Image.Resampling.NEAREST)


def composite_layers(layers: List[dict], cell: int) -> Image.Image:
    """从下往上叠。最底下一层不吃透明度，上面的层按各自透明度叠上去。"""
    n = snap_cell(cell)
    out = Image.new("RGBA", (n, n), (0, 0, 0, 0))
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
    n = snap_cell(cell)
    cols = 1
    for row in rows:
        cols = max(cols, len(row))
    sheet = Image.new("RGBA", (cols * n, max(1, len(rows)) * n), (0, 0, 0, 0))
    for y, frames in enumerate(rows):
        for x, fr in enumerate(frames):
            if fr is None:
                continue
            piece = resize_cell(fr, n)
            sheet.paste(piece, (x * n, y * n), piece)
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
