# utils/color_pick.py
# 全屏点选取色：先冻结各屏画面，再点任意像素。剪贴板写入 6 位 HEX（Photoshop 可粘贴）。
# 抓图必须在遮罩出现之前完成，否则会采到遮罩自己的颜色。

from __future__ import annotations

from PyQt5.QtCore import Qt, QRect, QPoint, QTimer
from PyQt5.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QPixmap, QCursor, QImage,
)
from PyQt5.QtWidgets import QApplication, QWidget

from utils.logger import get_logger

log = get_logger(__name__)

# Photoshop 取色器十六进制框认 6 位 HEX（# 是框外标签，带上会贴不进去）
def format_clip_hex(r, g, b) -> str:
    return "%02X%02X%02X" % (int(r) & 255, int(g) & 255, int(b) & 255)


def format_hex_hash(r, g, b) -> str:
    return "#" + format_clip_hex(r, g, b)

_MAG_SRC = 11          # 放大镜取样边长（奇数，中心正好是当前像素）
_MAG_SCALE = 8         # 每个源像素放大倍数
_MAG = _MAG_SRC * _MAG_SCALE


class ColorPickOverlay(QWidget):
    """盖住虚拟桌面：左键取色，右键 / Esc 取消。"""

    def __init__(self, on_picked, on_cancel=None):
        super().__init__()
        self.on_picked = on_picked
        self.on_cancel = on_cancel
        self._done = False
        self._img = QImage()
        self._hot = QColor(0, 0, 0)
        self._pos = QPoint(0, 0)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Window)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self._build_background()
        self.setGeometry(self._screen_geo)
        self.show()
        self.setGeometry(self._screen_geo)
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self._pos = self.mapFromGlobal(QCursor.pos())
        self._hot = self._color_at(self._pos)
        QTimer.singleShot(0, self._sync_cursor)

    def _sync_cursor(self):
        if self._done:
            return
        try:
            self._pos = self.mapFromGlobal(QCursor.pos())
            self._hot = self._color_at(self._pos)
            self.update()
        except Exception:
            pass

    def _build_background(self):
        screens = QApplication.screens() or [QApplication.primaryScreen()]
        virtual = QRect(screens[0].geometry())
        for s in screens[1:]:
            virtual = virtual.united(s.geometry())

        ref_dpr = 1.0
        try:
            g0 = screens[0].geometry()
            pm0 = screens[0].grabWindow(0)
            pm0.setDevicePixelRatio(1.0)
            if g0.width():
                ref_dpr = pm0.width() / g0.width()
        except Exception:
            ref_dpr = 1.0
        if not (ref_dpr > 0):
            ref_dpr = 1.0

        w = max(1, int(round(virtual.width() * ref_dpr)))
        h = max(1, int(round(virtual.height() * ref_dpr)))
        bg = QPixmap(w, h)
        bg.fill(Qt.black)
        painter = QPainter(bg)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        try:
            for s in screens:
                geo = s.geometry()
                try:
                    s_dpr = float(s.devicePixelRatio() or ref_dpr)
                except Exception:
                    s_dpr = ref_dpr
                if not (s_dpr > 0):
                    s_dpr = ref_dpr
                pm = s.grabWindow(0)
                pm.setDevicePixelRatio(1.0)
                tw = max(1, int(round(geo.width() * ref_dpr)))
                th = max(1, int(round(geo.height() * ref_dpr)))
                if abs(s_dpr - ref_dpr) > 1e-3:
                    pm = pm.scaled(tw, th, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                tx = int(round((geo.x() - virtual.x()) * ref_dpr))
                ty = int(round((geo.y() - virtual.y()) * ref_dpr))
                painter.drawPixmap(tx, ty, pm)
        finally:
            painter.end()
        bg.setDevicePixelRatio(1.0)
        self.bg = bg
        self.dpr = ref_dpr
        self._screen_geo = QRect(virtual)
        self._img = bg.toImage()

    def _clamp_phys(self, x, y):
        w = max(1, self._img.width())
        h = max(1, self._img.height())
        return max(0, min(w - 1, int(x))), max(0, min(h - 1, int(y)))

    def _color_at(self, pos: QPoint) -> QColor:
        d = self.dpr if self.dpr else 1.0
        x, y = self._clamp_phys(round(pos.x() * d), round(pos.y() * d))
        if self._img.isNull():
            return QColor(0, 0, 0)
        return QColor(self._img.pixel(x, y))

    def _finish(self, picked: bool):
        if self._done:
            return
        self._done = True
        color = QColor(self._hot)
        cb_ok = self.on_picked
        cb_no = self.on_cancel
        self.on_picked = None
        self.on_cancel = None
        try:
            self.hide()
        except Exception:
            pass
        if picked and callable(cb_ok):
            try:
                cb_ok(color.red(), color.green(), color.blue())
            except Exception:
                log.exception("取色回调失败")
        elif (not picked) and callable(cb_no):
            try:
                cb_no()
            except Exception:
                log.exception("取消取色回调失败")
        try:
            self.close()
            self.deleteLater()
        except Exception:
            pass

    def cancel(self):
        self._finish(False)

    def mouseMoveEvent(self, e):
        self._pos = e.pos()
        self._hot = self._color_at(self._pos)
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._pos = e.pos()
            self._hot = self._color_at(self._pos)
            self._finish(True)
        elif e.button() == Qt.RightButton:
            self._finish(False)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self._finish(False)

    def _hud_origin(self, pos: QPoint, w: int, h: int) -> QPoint:
        x = pos.x() + 20
        y = pos.y() + 20
        if x + w > self.width() - 8:
            x = pos.x() - 20 - w
        if y + h > self.height() - 8:
            y = pos.y() - 20 - h
        x = max(8, min(x, max(8, self.width() - w - 8)))
        y = max(8, min(y, max(8, self.height() - h - 8)))
        return QPoint(int(x), int(y))

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawPixmap(self.rect(), self.bg)
        pos = self._pos
        c = self._hot
        hexv = format_hex_hash(c.red(), c.green(), c.blue())
        clip = format_clip_hex(c.red(), c.green(), c.blue())

        mag = QPixmap(_MAG, _MAG)
        mag.fill(Qt.black)
        d = self.dpr if self.dpr else 1.0
        cx, cy = self._clamp_phys(round(pos.x() * d), round(pos.y() * d))
        half = _MAG_SRC // 2
        mp = QPainter(mag)
        mp.setRenderHint(QPainter.SmoothPixmapTransform, False)
        for iy in range(_MAG_SRC):
            for ix in range(_MAG_SRC):
                px, py = self._clamp_phys(cx - half + ix, cy - half + iy)
                mp.fillRect(
                    ix * _MAG_SCALE, iy * _MAG_SCALE, _MAG_SCALE, _MAG_SCALE,
                    QColor(self._img.pixel(px, py)) if not self._img.isNull() else Qt.black,
                )
        mp.setPen(QPen(QColor("#ffffff"), 1))
        mid = half * _MAG_SCALE
        mp.drawRect(mid, mid, _MAG_SCALE - 1, _MAG_SCALE - 1)
        mp.end()

        pad = 8
        text_h = 36
        hud_w = _MAG + pad * 2
        hud_h = _MAG + pad * 2 + text_h
        origin = self._hud_origin(pos, hud_w, hud_h)
        hx, hy = origin.x(), origin.y()
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(0, 0, 0, 180), 1))
        p.setBrush(QBrush(QColor(20, 24, 36, 230)))
        p.drawRoundedRect(hx, hy, hud_w, hud_h, 8, 8)
        p.drawPixmap(hx + pad, hy + pad, mag)
        p.setPen(QPen(QColor("#cfe0ff")))
        f = QFont("Microsoft YaHei UI", 10)
        f.setBold(True)
        p.setFont(f)
        p.drawText(hx + pad, hy + pad + _MAG + 16, hexv)
        f.setBold(False)
        f.setPointSize(9)
        p.setFont(f)
        p.setPen(QPen(QColor("#9fb0d7")))
        p.drawText(hx + pad, hy + pad + _MAG + 32, clip)

        # 准星：不挡当前像素
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setPen(QPen(QColor(255, 255, 255, 200), 1))
        x, y = pos.x(), pos.y()
        p.drawLine(x - 12, y, x - 3, y)
        p.drawLine(x + 3, y, x + 12, y)
        p.drawLine(x, y - 12, x, y - 3)
        p.drawLine(x, y + 3, x, y + 12)
