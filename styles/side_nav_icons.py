# styles/side_nav_icons.py
# 侧栏导航彩色图标：圆角色底 + 白色矢量符号（不依赖 emoji 宽窄）

from PyQt5.QtCore import Qt, QRectF, QPointF, QSize
from PyQt5.QtGui import (
    QIcon, QPixmap, QPainter, QColor, QPen, QBrush, QPainterPath, QLinearGradient,
)


# 每项固定品牌色（亮/暗主题都够醒目）
_NAV_COLORS = {
    "overview":  ("#3B82F6", "#2563EB"),   # 蓝 · 总览
    "fast":      ("#22C55E", "#16A34A"),   # 绿 · 速存
    "video":     ("#F43F5E", "#E11D48"),   # 玫红 · 视频
    "gallery":   ("#A855F7", "#9333EA"),   # 紫 · 图集
    "paste":     ("#EAB308", "#CA8A04"),   # 黄 · 粘贴（避开速存绿 / 语音编辑青绿）
    "voice":     ("#2DD4BF", "#14B8A6"),   # 青绿 · 语音编辑
    "voice_clone":("#8B5CF6", "#6D28D9"),  # 紫 · 语音克隆
    "game_assist":("#0EA5E9", "#0284C7"),  # 天蓝 · 游戏助手
    "digital_human":("#E879F9", "#C026D3"),  # 品红 · 数字人
    "prompt":    ("#38BDF8", "#0284C7"),   # 天蓝 · 提示词
    "points":    ("#F59E0B", "#D97706"),   # 琥珀 · 积分
    "shot":      ("#06B6D4", "#0891B2"),   # 青蓝 · 截图
    "ratio":     ("#6366F1", "#4F46E5"),   # 靛 · 比例（实用工具子页图标）
    "dir_link":  ("#F97316", "#EA580C"),   # 橙 · 目录（实用工具子页图标）
    "tz_fx":     ("#10B981", "#059669"),   # 翠绿 · 时区汇率（实用工具子页图标）
    "region_rec":("#EF4444", "#DC2626"),   # 录制红 · 区域录屏
    "img_proc":  ("#EC4899", "#DB2777"),   # 品红 · 图片处理
    "about":     ("#64748B", "#475569"),   # 石板灰 · 关于
}


def _fill_badge(p: QPainter, s: float, c0: str, c1: str):
    m = s * 0.06
    body = QRectF(m, m, s - 2 * m, s - 2 * m)
    grad = QLinearGradient(body.topLeft(), body.bottomRight())
    grad.setColorAt(0, QColor(c0))
    grad.setColorAt(1, QColor(c1))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(grad))
    p.drawRoundedRect(body, s * 0.24, s * 0.24)
    # 顶部高光
    hi = QLinearGradient(0, m, 0, m + s * 0.4)
    hi.setColorAt(0, QColor(255, 255, 255, 55))
    hi.setColorAt(1, QColor(255, 255, 255, 0))
    p.setBrush(QBrush(hi))
    p.drawRoundedRect(QRectF(m + 0.5, m + 0.5, s - 2 * m - 1, s * 0.42), s * 0.2, s * 0.2)
    return body


def _w(p: QPainter, color=None):
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(color or QColor("#FFFFFF")))


def _draw_overview(p, s):
    """显示器 / 仪表盘"""
    _w(p)
    # 外框
    p.drawRoundedRect(QRectF(s * 0.22, s * 0.24, s * 0.56, s * 0.40), 2.2, 2.2)
    # 屏内横条
    p.setBrush(QBrush(QColor(255, 255, 255, 90)))
    p.drawRoundedRect(QRectF(s * 0.30, s * 0.32, s * 0.40, s * 0.08), 1.2, 1.2)
    p.drawRoundedRect(QRectF(s * 0.30, s * 0.44, s * 0.28, s * 0.08), 1.2, 1.2)
    _w(p)
    # 底座
    p.drawRoundedRect(QRectF(s * 0.40, s * 0.64, s * 0.20, s * 0.06), 1.0, 1.0)
    p.drawRoundedRect(QRectF(s * 0.32, s * 0.70, s * 0.36, s * 0.05), 1.5, 1.5)


def _draw_fast(p, s):
    """下载箭头进托盘"""
    _w(p)
    # 箭头竖杆
    p.drawRoundedRect(QRectF(s * 0.44, s * 0.22, s * 0.12, s * 0.32), 1.5, 1.5)
    # 箭头三角
    tri = QPainterPath()
    tri.moveTo(s * 0.28, s * 0.48)
    tri.lineTo(s * 0.72, s * 0.48)
    tri.lineTo(s * 0.50, s * 0.70)
    tri.closeSubpath()
    p.drawPath(tri)
    # 底托
    p.drawRoundedRect(QRectF(s * 0.28, s * 0.72, s * 0.44, s * 0.08), 1.5, 1.5)


def _draw_video(p, s):
    """播放三角（圆角胶囊底已由 badge 提供，这里画白三角）"""
    _w(p)
    path = QPainterPath()
    path.moveTo(s * 0.38, s * 0.28)
    path.lineTo(s * 0.38, s * 0.72)
    path.lineTo(s * 0.72, s * 0.50)
    path.closeSubpath()
    p.drawPath(path)


def _draw_gallery(p, s):
    """2×2 图块网格"""
    _w(p)
    gap, sz, o = s * 0.06, s * 0.26, s * 0.22
    for i in range(2):
        for j in range(2):
            p.drawRoundedRect(
                QRectF(o + j * (sz + gap), o + i * (sz + gap), sz, sz), 2.0, 2.0
            )


def _draw_paste(p, s):
    """剪贴板"""
    _w(p)
    # 板身
    p.drawRoundedRect(QRectF(s * 0.28, s * 0.28, s * 0.44, s * 0.50), 2.5, 2.5)
    # 夹子
    p.setBrush(QBrush(QColor(255, 255, 255, 220)))
    p.drawRoundedRect(QRectF(s * 0.36, s * 0.20, s * 0.28, s * 0.14), 2.0, 2.0)
    # 板内线
    p.setBrush(QBrush(QColor(255, 255, 255, 100)))
    p.drawRoundedRect(QRectF(s * 0.36, s * 0.44, s * 0.28, s * 0.05), 1.0, 1.0)
    p.drawRoundedRect(QRectF(s * 0.36, s * 0.54, s * 0.22, s * 0.05), 1.0, 1.0)


def _draw_voice(p, s):
    """麦克风"""
    _w(p)
    # 麦头
    p.drawRoundedRect(QRectF(s * 0.38, s * 0.20, s * 0.24, s * 0.36), s * 0.12, s * 0.12)
    # 支架弧
    pen = QPen(QColor("#FFFFFF"), max(1.6, s * 0.08))
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(s * 0.28, s * 0.36, s * 0.44, s * 0.36), 200 * 16, 140 * 16)
    _w(p)
    # 竖杆 + 底座
    p.drawRoundedRect(QRectF(s * 0.46, s * 0.62, s * 0.08, s * 0.12), 1.2, 1.2)
    p.drawRoundedRect(QRectF(s * 0.32, s * 0.74, s * 0.36, s * 0.07), 2.0, 2.0)


def _draw_voice_clone(p, s):
    """扬声器 + 声波：语音克隆"""
    _w(p)
    p.drawRoundedRect(QRectF(s * 0.22, s * 0.38, s * 0.12, s * 0.24), 1.5, 1.5)
    tri = QPainterPath()
    tri.moveTo(s * 0.32, s * 0.36)
    tri.lineTo(s * 0.50, s * 0.22)
    tri.lineTo(s * 0.50, s * 0.78)
    tri.lineTo(s * 0.32, s * 0.64)
    tri.closeSubpath()
    p.drawPath(tri)
    pen = QPen(QColor("#FFFFFF"), max(1.4, s * 0.07))
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(s * 0.48, s * 0.30, s * 0.28, s * 0.40), -70 * 16, 140 * 16)
    p.drawArc(QRectF(s * 0.54, s * 0.38, s * 0.22, s * 0.24), -55 * 16, 110 * 16)


def _draw_game_assist(p, s):
    """手柄：游戏助手"""
    p.setBrush(Qt.NoBrush)
    pen = QPen(QColor("#FFFFFF"), max(1.6, s * 0.07))
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.drawRoundedRect(QRectF(s * 0.20, s * 0.34, s * 0.60, s * 0.32), s * 0.16, s * 0.16)
    _w(p)
    p.drawRoundedRect(QRectF(s * 0.30, s * 0.44, s * 0.16, s * 0.05), 1.0, 1.0)
    p.drawRoundedRect(QRectF(s * 0.355, s * 0.39, s * 0.05, s * 0.16), 1.0, 1.0)
    p.drawEllipse(QRectF(s * 0.56, s * 0.40, s * 0.08, s * 0.08))
    p.drawEllipse(QRectF(s * 0.66, s * 0.48, s * 0.08, s * 0.08))


def _draw_digital_human(p, s):
    """人像轮廓：数字人"""
    _w(p)
    p.drawEllipse(QRectF(s * 0.36, s * 0.20, s * 0.28, s * 0.28))
    body = QPainterPath()
    body.moveTo(s * 0.28, s * 0.78)
    body.quadTo(s * 0.28, s * 0.50, s * 0.50, s * 0.50)
    body.quadTo(s * 0.72, s * 0.50, s * 0.72, s * 0.78)
    body.closeSubpath()
    p.drawPath(body)


def _draw_prompt(p, s):
    """左右对照文稿：提示词（原稿 | 新稿）"""
    _w(p)
    p.drawRoundedRect(QRectF(s * 0.26, s * 0.22, s * 0.48, s * 0.56), 2.2, 2.2)
    p.setBrush(QBrush(QColor(255, 255, 255, 90)))
    p.drawRoundedRect(QRectF(s * 0.485, s * 0.28, s * 0.03, s * 0.44), 1.0, 1.0)
    p.setBrush(QBrush(QColor(255, 255, 255, 210)))
    p.drawRoundedRect(QRectF(s * 0.31, s * 0.34, s * 0.14, s * 0.05), 1.0, 1.0)
    p.drawRoundedRect(QRectF(s * 0.31, s * 0.44, s * 0.12, s * 0.05), 1.0, 1.0)
    p.drawRoundedRect(QRectF(s * 0.56, s * 0.34, s * 0.14, s * 0.05), 1.0, 1.0)
    p.drawRoundedRect(QRectF(s * 0.56, s * 0.44, s * 0.11, s * 0.05), 1.0, 1.0)


def _draw_points(p, s):
    """五角星"""
    _w(p)
    import math
    cx, cy, r = s * 0.50, s * 0.52, s * 0.28
    path = QPainterPath()
    for i in range(5):
        a = math.radians(-90 + i * 72)
        b = math.radians(-90 + i * 72 + 36)
        x1, y1 = cx + r * math.cos(a), cy + r * math.sin(a)
        x2, y2 = cx + r * 0.42 * math.cos(b), cy + r * 0.42 * math.sin(b)
        if i == 0:
            path.moveTo(x1, y1)
        else:
            path.lineTo(x1, y1)
        path.lineTo(x2, y2)
    path.closeSubpath()
    p.drawPath(path)


def _draw_shot(p, s):
    """相机"""
    _w(p)
    p.drawRoundedRect(QRectF(s * 0.20, s * 0.34, s * 0.60, s * 0.40), 3.0, 3.0)
    p.drawRoundedRect(QRectF(s * 0.34, s * 0.24, s * 0.20, s * 0.14), 2.0, 2.0)
    # 镜头
    p.setBrush(QBrush(QColor(255, 255, 255, 90)))
    p.drawEllipse(QRectF(s * 0.36, s * 0.40, s * 0.28, s * 0.28))
    _w(p)
    p.drawEllipse(QRectF(s * 0.42, s * 0.46, s * 0.16, s * 0.16))


def _draw_ratio(p, s):
    """双向箭头 / 比例"""
    _w(p)
    # 横杆
    p.drawRoundedRect(QRectF(s * 0.22, s * 0.46, s * 0.56, s * 0.08), 1.5, 1.5)
    # 左三角
    L = QPainterPath()
    L.moveTo(s * 0.38, s * 0.30)
    L.lineTo(s * 0.22, s * 0.50)
    L.lineTo(s * 0.38, s * 0.70)
    L.closeSubpath()
    p.drawPath(L)
    # 右三角
    R = QPainterPath()
    R.moveTo(s * 0.62, s * 0.30)
    R.lineTo(s * 0.78, s * 0.50)
    R.lineTo(s * 0.62, s * 0.70)
    R.closeSubpath()
    p.drawPath(R)


def _draw_dir_link(p, s):
    """文件夹"""
    _w(p)
    # 页签
    p.drawRoundedRect(QRectF(s * 0.22, s * 0.28, s * 0.28, s * 0.14), 2.0, 2.0)
    # 主体
    p.drawRoundedRect(QRectF(s * 0.22, s * 0.36, s * 0.56, s * 0.40), 3.0, 3.0)
    # 链接小弧
    pen = QPen(QColor(255, 255, 255), max(1.4, s * 0.07))
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(s * 0.48, s * 0.48, s * 0.22, s * 0.18), 30 * 16, 200 * 16)


def _draw_tz_fx(p, s):
    """地球 / 时钟"""
    _w(p)
    p.drawEllipse(QRectF(s * 0.24, s * 0.24, s * 0.52, s * 0.52))
    p.setBrush(Qt.NoBrush)
    pen = QPen(QColor(255, 255, 255, 200), max(1.2, s * 0.06))
    p.setPen(pen)
    # 经纬
    p.drawEllipse(QRectF(s * 0.36, s * 0.24, s * 0.28, s * 0.52))
    p.drawLine(QPointF(s * 0.24, s * 0.50), QPointF(s * 0.76, s * 0.50))
    p.drawLine(QPointF(s * 0.28, s * 0.38), QPointF(s * 0.72, s * 0.38))
    p.drawLine(QPointF(s * 0.28, s * 0.62), QPointF(s * 0.72, s * 0.62))


def _draw_region_rec(p, s):
    """区域框 + 录制圆点"""
    _w(p)
    # 外框（选区）
    p.setBrush(Qt.NoBrush)
    pen = QPen(QColor("#FFFFFF"), max(1.5, s * 0.08))
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.drawRoundedRect(QRectF(s * 0.22, s * 0.26, s * 0.56, s * 0.42), 2.5, 2.5)
    # 角点感
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor("#FFFFFF")))
    for cx, cy in (
        (s * 0.22, s * 0.26), (s * 0.78, s * 0.26),
        (s * 0.22, s * 0.68), (s * 0.78, s * 0.68),
    ):
        p.drawEllipse(QPointF(cx, cy), s * 0.04, s * 0.04)
    # REC 圆点
    p.setBrush(QBrush(QColor("#FFFFFF")))
    p.drawEllipse(QRectF(s * 0.42, s * 0.38, s * 0.16, s * 0.16))


def _draw_img_proc(p, s):
    """图片 + 裁剪角：图片处理"""
    _w(p)
    # 画框
    p.drawRoundedRect(QRectF(s * 0.22, s * 0.28, s * 0.48, s * 0.40), 2.5, 2.5)
    # 山形
    path = QPainterPath()
    path.moveTo(s * 0.30, s * 0.58)
    path.lineTo(s * 0.42, s * 0.42)
    path.lineTo(s * 0.52, s * 0.52)
    path.lineTo(s * 0.60, s * 0.44)
    path.lineTo(s * 0.66, s * 0.58)
    path.closeSubpath()
    p.setBrush(QBrush(QColor(255, 255, 255, 200)))
    p.drawPath(path)
    # 小太阳
    _w(p)
    p.drawEllipse(QRectF(s * 0.54, s * 0.34, s * 0.10, s * 0.10))
    # 右下裁剪角标
    pen = QPen(QColor("#FFFFFF"), max(1.5, s * 0.07))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawLine(QPointF(s * 0.62, s * 0.70), QPointF(s * 0.78, s * 0.70))
    p.drawLine(QPointF(s * 0.78, s * 0.70), QPointF(s * 0.78, s * 0.54))


def _draw_about(p, s):
    """信息 i / 关于"""
    _w(p)
    # 圆
    p.setBrush(Qt.NoBrush)
    pen = QPen(QColor("#FFFFFF"), max(1.6, s * 0.08))
    p.setPen(pen)
    p.drawEllipse(QRectF(s * 0.22, s * 0.22, s * 0.56, s * 0.56))
    # 点 + 竖线
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor("#FFFFFF")))
    p.drawEllipse(QRectF(s * 0.46, s * 0.32, s * 0.08, s * 0.08))
    p.drawRoundedRect(QRectF(s * 0.46, s * 0.44, s * 0.08, s * 0.24), 1.2, 1.2)


_DRAWERS = {
    "overview": _draw_overview,
    "fast": _draw_fast,
    "video": _draw_video,
    "gallery": _draw_gallery,
    "paste": _draw_paste,
    "voice": _draw_voice,
    "voice_clone": _draw_voice_clone,
    "game_assist": _draw_game_assist,
    "digital_human": _draw_digital_human,
    "prompt": _draw_prompt,
    "points": _draw_points,
    "shot": _draw_shot,
    "ratio": _draw_ratio,
    "dir_link": _draw_dir_link,
    "tz_fx": _draw_tz_fx,
    "region_rec": _draw_region_rec,
    "img_proc": _draw_img_proc,
    "about": _draw_about,
}


def paint_header_rename_icon(size: int = 14, color: str = "#9fb0d7") -> QIcon:
    """侧栏分区改名：线框铅笔，不带圆角色底。"""
    size = max(10, int(size))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    s = float(size)
    c = QColor(color)
    pen = QPen(c, max(1.2, s * 0.10))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    # 45° 铅笔外轮廓：左下笔尖 → 右上橡皮
    body = QPainterPath()
    body.moveTo(s * 0.18, s * 0.82)
    body.lineTo(s * 0.32, s * 0.58)
    body.lineTo(s * 0.58, s * 0.32)
    body.lineTo(s * 0.68, s * 0.22)
    body.lineTo(s * 0.82, s * 0.36)
    body.lineTo(s * 0.72, s * 0.46)
    body.lineTo(s * 0.46, s * 0.72)
    body.closeSubpath()
    p.drawPath(body)
    # 笔尖切线、橡皮分界
    p.drawLine(QPointF(s * 0.32, s * 0.58), QPointF(s * 0.46, s * 0.72))
    p.drawLine(QPointF(s * 0.58, s * 0.32), QPointF(s * 0.72, s * 0.46))
    p.end()
    return QIcon(pm)


def paint_header_clock_icon(size: int = 18, color: str = "#9fb0d7") -> QIcon:
    """顶栏「时间」线框钟：圆盘 + 时针分针，不带色底。"""
    size = max(14, int(size))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    s = float(size)
    c = QColor(color)
    pen = QPen(c, max(1.6, s * 0.09))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    m = s * 0.14
    p.drawEllipse(QRectF(m, m, s - 2 * m, s - 2 * m))
    cx = cy = s * 0.50
    # 12 点刻度短线
    p.drawLine(QPointF(cx, m + s * 0.04), QPointF(cx, m + s * 0.12))
    # 时针（约 10 点方向）/ 分针（约 2 点方向）
    p.drawLine(QPointF(cx, cy), QPointF(cx - s * 0.10, cy - s * 0.12))
    p.drawLine(QPointF(cx, cy), QPointF(cx + s * 0.16, cy - s * 0.04))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(c))
    p.drawEllipse(QPointF(cx, cy), s * 0.05, s * 0.05)
    p.end()
    return QIcon(pm)


def paint_side_nav_icon(key: str, size: int = 20) -> QIcon:
    """绘制侧栏导航彩色图标。key 见 _NAV_COLORS / _DRAWERS。"""
    size = max(14, int(size))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)

    s = float(size)
    c0, c1 = _NAV_COLORS.get(key, ("#64748B", "#475569"))
    _fill_badge(p, s, c0, c1)

    drawer = _DRAWERS.get(key)
    if drawer:
        drawer(p, s)

    p.end()
    return QIcon(pm)


def side_nav_icon_size() -> QSize:
    """侧栏图标设计基准 20px（物理大小由启动时 QT_SCALE_FACTOR 全局放大）。"""
    return QSize(20, 20)
