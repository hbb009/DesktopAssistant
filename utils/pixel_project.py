# utils/pixel_project.py
# 第十三阶段：像素画板「动效」工程文件 .pxa
# 格式：zip 容器
#   manifest.json —— {"format": "pixel-walk", "version": 1, 格子、帧数、每帧时长、名称、对齐、帧率、循环、调色盘、自定义色、当前帧…}
#   base.png      —— 底图（RGBA，无损）
#   frames/001.png … —— 每帧一张（RGBA，无损）
# 写入是原子的：先写同目录临时文件、刷盘，再 os.replace 覆盖；中途出错原文件不受影响。
# 读取先全部校验，任何问题都抛 ProjectError（带中文说明），调用方据此提示，不改当前状态。
from __future__ import annotations

import io
import json
import os
import tempfile
import zipfile

from PIL import Image

PXA_EXT = ".pxa"
PXB_EXT = ".pxb"                 # 第十四阶段：画板工程
PXB_FORMAT = "pixel-art"
PXB_VERSION = 1
ART_DIRS = ("down", "left", "up", "right")
MAX_LAYERS = 6
WRONG_KIND = {
    "walk": "这是画板工程(.pxb)，请在画板页打开",
    "art": "这是动效工程(.pxa)，请在动效页打开",
}
PXA_FORMAT = "pixel-walk"
PXA_VERSION = 1
CELL_MIN, CELL_MAX = 8, 512
MAX_FRAMES = 64
_MAX_UNPACKED = 512 * 1024 * 1024   # 解压总量上限，防坏文件/压缩炸弹


class ProjectError(Exception):
    """工程文件读写失败；str(e) 是给用户看的中文说明。"""


def _png_bytes(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.convert("RGBA").save(buf, "PNG", optimize=False, compress_level=6)
    return buf.getvalue()


def frame_name(i: int) -> str:
    return f"frames/{i + 1:03d}.png"


def save_project(path: str, data: dict) -> None:
    """data: cell(w,h)、base(PIL)、frames[PIL]、holds[int]，其余元数据原样写进 manifest。"""
    path = os.path.abspath(path)
    frames = list(data["frames"])
    if not frames or len(frames) > MAX_FRAMES:
        raise ProjectError(f"帧数必须是 1–{MAX_FRAMES}")
    cw, ch = (int(v) for v in data["cell"])
    holds = [max(1, min(4, int(v))) for v in list(data.get("holds") or [])[:len(frames)]]
    holds += [1] * (len(frames) - len(holds))
    manifest = {k: v for k, v in data.items() if k not in ("base", "frames", "cell", "holds")}
    manifest.update({
        "format": PXA_FORMAT, "kind": "walk", "version": PXA_VERSION,
        "cell_w": cw, "cell_h": ch, "frame_count": len(frames), "holds": holds,
        "base": "base.png", "frames": [frame_name(i) for i in range(len(frames))],
    })
    images = [("base.png", data["base"])] + [(frame_name(i), im) for i, im in enumerate(frames)]
    _atomic_write(path, manifest, images)


def _atomic_write(path: str, manifest: dict, images) -> None:
    folder = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".~" + os.path.basename(path) + ".", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "wb") as fh:
            with zipfile.ZipFile(fh, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
                for name, im in images:
                    z.writestr(name, _png_bytes(im))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _read_png(z: zipfile.ZipFile, name: str, cell) -> Image.Image:
    try:
        raw = z.read(name)
        im = Image.open(io.BytesIO(raw))
        im.load()
    except KeyError:
        raise ProjectError(f"工程里缺少 {name}")
    except Exception as e:
        raise ProjectError(f"{name} 不是有效的 PNG（{type(e).__name__}）")
    if im.size != tuple(cell):
        raise ProjectError(f"{name} 尺寸 {im.size[0]}×{im.size[1]} 与工程格子 {cell[0]}×{cell[1]} 不符")
    return im.convert("RGBA")


def project_kind_of_path(path: str) -> str:
    ext = os.path.splitext(path or "")[1].lower()
    return {PXA_EXT: "walk", PXB_EXT: "art"}.get(ext, "")


def _manifest_kind(m) -> str:
    if not isinstance(m, dict):
        return ""
    kind = m.get("kind")
    if m.get("format") == PXA_FORMAT and kind in (None, "walk"):
        return "walk"          # 第十三阶段的 .pxa 没有 kind 字段
    if m.get("format") == PXB_FORMAT and kind in (None, "art"):
        return "art"
    return ""


def _open_checked(path: str, want: str):
    """打开 zip、读 manifest，并校验种类/版本/格子尺寸。返回 (zipfile, manifest, cell)。"""
    if project_kind_of_path(path) and project_kind_of_path(path) != want:
        raise ProjectError(WRONG_KIND[want])
    if not os.path.isfile(path):
        raise ProjectError("文件不存在")
    try:
        z = zipfile.ZipFile(path, "r")
    except (zipfile.BadZipFile, OSError) as e:
        raise ProjectError(f"不是有效的工程文件（{type(e).__name__}）")
    try:
        try:
            if sum(i.file_size for i in z.infolist()) > _MAX_UNPACKED:
                raise ProjectError("工程文件过大或已损坏")
            m = json.loads(z.read("manifest.json").decode("utf-8"))
        except ProjectError:
            raise
        except KeyError:
            raise ProjectError("工程里缺少 manifest.json")
        except Exception as e:
            raise ProjectError(f"manifest.json 无法解析（{type(e).__name__}）")
        kind = _manifest_kind(m)
        if kind and kind != want:
            raise ProjectError(WRONG_KIND[want])
        if kind != want:
            raise ProjectError("不是像素动效工程文件" if want == "walk" else "不是像素画板工程文件")
        ver = m.get("version")
        top = PXA_VERSION if want == "walk" else PXB_VERSION
        if not isinstance(ver, int) or isinstance(ver, bool) or ver < 1:
            raise ProjectError("工程版本号无效")
        if ver > top:
            raise ProjectError(f"工程版本 {ver} 比本程序支持的 {top} 新，请升级后再打开")
        try:
            cell = (int(m["cell_w"]), int(m["cell_h"]))
        except Exception:
            raise ProjectError("manifest 缺少格子尺寸")
        if not all(CELL_MIN <= v <= CELL_MAX for v in cell):
            raise ProjectError(f"格子尺寸 {cell[0]}×{cell[1]} 超出 {CELL_MIN}–{CELL_MAX}")
    except BaseException:
        z.close()
        raise
    return z, m, cell


def load_project(path: str) -> dict:
    """读并校验动效工程 .pxa，返回 dict（cell、base、frames、holds 以及 manifest 其余字段）。"""
    z, m, cell = _open_checked(path, "walk")
    with z:
        try:
            n = int(m["frame_count"])
        except Exception:
            raise ProjectError("manifest 缺少帧数")
        if not 1 <= n <= MAX_FRAMES:
            raise ProjectError(f"帧数 {n} 超出 1–{MAX_FRAMES}")
        names = m.get("frames") if isinstance(m.get("frames"), list) else [frame_name(i) for i in range(n)]
        if len(names) != n:
            raise ProjectError("帧列表与帧数不一致")
        base = _read_png(z, str(m.get("base") or "base.png"), cell)
        frames = [_read_png(z, str(nm), cell) for nm in names]
    holds = []
    for v in (m.get("holds") if isinstance(m.get("holds"), list) else [])[:n]:
        try:
            holds.append(max(1, min(4, int(v))))
        except (TypeError, ValueError):
            holds.append(1)
    holds += [1] * (n - len(holds))
    out = {k: v for k, v in m.items() if k not in ("base", "frames", "holds", "cell_w", "cell_h")}
    out.update({"cell": cell, "base": base, "frames": frames, "holds": holds})
    try:
        out["frame_i"] = max(0, min(n - 1, int(m.get("frame_i", 0))))
    except (TypeError, ValueError):
        out["frame_i"] = 0
    return out


# ---------- 第十四阶段：画板工程 .pxb ----------
# manifest: {"format": "pixel-art", "kind": "art", "version": 1, cell_w, cell_h, dir,
#            layers: {方向: [{"name", "visible", "opacity", "file"} …底层在前]}, active: {方向: 层号}, 其余设置}
# 图片: layers/<方向>/01.png …

def layer_name(direction: str, i: int) -> str:
    return f"layers/{direction}/{i + 1:02d}.png"


def save_art_project(path: str, data: dict) -> None:
    """data: cell、layers{方向: [{name,image,opacity,visible,…}]}、active{方向: 序号}，其余原样写进 manifest。"""
    path = os.path.abspath(path)
    cw, ch = (int(v) for v in data["cell"])
    layers = data["layers"]
    meta, images = {}, []
    for d in ART_DIRS:
        ls = list(layers.get(d) or [])
        if not 1 <= len(ls) <= MAX_LAYERS:
            raise ProjectError(f"每个方向图层数必须是 1–{MAX_LAYERS}")
        rows = []
        for i, layer in enumerate(ls):
            row = {k: v for k, v in layer.items() if k != "image" and isinstance(v, (str, int, float, bool))}
            row["file"] = layer_name(d, i)
            rows.append(row)
            images.append((row["file"], layer["image"]))
        meta[d] = rows
    manifest = {k: v for k, v in data.items() if k not in ("cell", "layers")}
    manifest.update({"format": PXB_FORMAT, "kind": "art", "version": PXB_VERSION,
                     "cell_w": cw, "cell_h": ch, "layers": meta})
    _atomic_write(path, manifest, images)


def load_art_project(path: str) -> dict:
    """读并校验画板工程 .pxb；返回 cell、layers{方向: [{name,image,opacity,visible}]}、active、dir 及其余字段。"""
    z, m, cell = _open_checked(path, "art")
    with z:
        meta = m.get("layers")
        if not isinstance(meta, dict):
            raise ProjectError("manifest 缺少图层信息")
        layers = {}
        for d in ART_DIRS:
            rows = meta.get(d)
            if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_LAYERS:
                raise ProjectError(f"方向 {d} 的图层数必须是 1–{MAX_LAYERS}")
            out = []
            for i, row in enumerate(rows):
                if not isinstance(row, dict):
                    raise ProjectError(f"方向 {d} 第 {i + 1} 层信息无效")
                try:
                    opacity = max(0.0, min(1.0, float(row.get("opacity", 1.0))))
                except (TypeError, ValueError):
                    opacity = 1.0
                layer = {k: v for k, v in row.items() if k not in ("file", "image")}
                layer.update({
                    "name": str(row.get("name") or f"图层{i + 1}"),
                    "visible": bool(row.get("visible", True)),
                    "opacity": opacity,
                    "image": _read_png(z, str(row.get("file") or layer_name(d, i)), cell),
                })
                out.append(layer)
            layers[d] = out
    active = {}
    raw = m.get("active") if isinstance(m.get("active"), dict) else {}
    for d in ART_DIRS:
        try:
            active[d] = max(0, min(len(layers[d]) - 1, int(raw.get(d, 0))))
        except (TypeError, ValueError):
            active[d] = 0
    out = {k: v for k, v in m.items() if k not in ("layers", "active", "cell_w", "cell_h")}
    out.update({"cell": cell, "layers": layers, "active": active,
                "dir": m.get("dir") if m.get("dir") in ART_DIRS else "down"})
    return out
