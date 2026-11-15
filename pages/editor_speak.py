# pages/editor_speak.py
# 文本编辑页的朗读：复用阅读器 CosyReader 引擎，不改阅读器行为。
# 播放走独立 MCI 别名，避免和已打开的阅读器抢同一条通道。

from __future__ import annotations

import os
import sys
import threading
import time

from PyQt5.QtCore import QObject, Qt, QSize, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PyQt5.QtWidgets import QPushButton

from styles.style_all import STD_BUTTON_H, apply_std_button, tk
from utils.app_paths import app_root

SPEAK_BG = "#7DB9F0"
SPEAK_HOVER = "#96CCF5"
SPEAK_PRESS = "#5EA3E0"
SPEAK_FG = "#10233A"
_ALIAS = "edtwav"
_TIP = "有选区读选区，否则从光标读到文末"


def _ensure_reader_path():
    root = app_root()
    mv = os.path.join(root, "MarkdownViewer")
    if os.path.isdir(mv) and mv not in sys.path:
        sys.path.insert(0, mv)
    return root


def speakable_plain(src):
    """去掉 Markdown 标记，只留下朗读用的可见正文。失败则退回原文。"""
    raw = (src or "").replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")
    if not raw.strip():
        return ""
    try:
        _ensure_reader_path()
        from md_view import parse_document, visible_text
    except Exception:
        return raw.strip()
    try:
        blocks, meta = parse_document(raw)
        refs = (meta or {}).get("refs") or {}
    except Exception:
        try:
            return visible_text(raw).strip()
        except Exception:
            return raw.strip()

    parts = []

    def inline(text):
        try:
            return (visible_text(text or "", refs) or "").strip()
        except Exception:
            return (text or "").strip()

    def walk(items):
        for block in items or []:
            if not isinstance(block, dict):
                continue
            kind = block.get("t")
            if kind == "h":
                t = inline(block.get("text") or "")
                if t:
                    parts.append(t)
            elif kind == "p":
                t = inline(block.get("text") or "")
                if t:
                    parts.append(t)
            elif kind == "quote":
                walk(block.get("blocks") or [])
            elif kind == "callout":
                t = inline(block.get("title") or "")
                if t:
                    parts.append(t)
                walk(block.get("blocks") or [])
            elif kind in ("code", "math"):
                t = (block.get("text") or "").strip()
                if t:
                    parts.append(t)
            elif kind in ("ul", "ol"):
                for item in block.get("items") or []:
                    if isinstance(item, dict):
                        walk(item.get("blocks") or [])
            elif kind == "table":
                for row in block.get("rows") or []:
                    cells = [inline(c if isinstance(c, str) else str(c or "")) for c in (row or [])]
                    line = " ".join(c for c in cells if c)
                    if line:
                        parts.append(line)
            elif kind in ("fm", "hr"):
                continue

    walk(blocks)
    text = "\n".join(parts).strip()
    return text or raw.strip()


def make_speak_icon(kind, color, size=12):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.NoPen)
    if kind == "pause":
        bar = max(2, size // 5)
        gap = max(2, size // 5)
        x = (size - (bar * 2 + gap)) // 2
        painter.drawRoundedRect(x, 1, bar, size - 2, 1, 1)
        painter.drawRoundedRect(x + bar + gap, 1, bar, size - 2, 1, 1)
    else:
        path = QPainterPath()
        path.moveTo(size * 0.30, size * 0.14)
        path.lineTo(size * 0.30, size * 0.86)
        path.lineTo(size * 0.88, size * 0.50)
        path.closeSubpath()
        painter.drawPath(path)
    painter.end()
    return QIcon(pm)


def _speak_on_qss():
    inner = STD_BUTTON_H - 4
    return (
        'QPushButton[kind="std"] {'
        " background: %s; border: 1px solid %s; color: %s;"
        " padding: 1px 4px; min-height: %spx; max-height: %spx;"
        " min-width: 0px; font-size: 12px; font-weight: 600; border-radius: 7px;"
        "}"
        'QPushButton[kind="std"]:hover { background: %s; border-color: %s; color: %s; }'
        'QPushButton[kind="std"]:pressed { background: %s; border-color: %s; }'
    ) % (SPEAK_BG, SPEAK_PRESS, SPEAK_FG, inner, inner, SPEAK_HOVER, SPEAK_BG, SPEAK_FG, SPEAK_PRESS, SPEAK_PRESS)


def style_speak_button(btn, on, paused=False):
    apply_std_button(btn)
    if on:
        btn.setStyleSheet(_speak_on_qss())
        color = SPEAK_FG
    else:
        btn.setStyleSheet("")
        apply_std_button(btn)
        try:
            color = tk("text")
        except Exception:
            color = "#e8e8e8"
    icon = "play" if (not on or paused) else "pause"
    btn.setIcon(make_speak_icon(icon, color))
    btn.setIconSize(QSize(10, 10))
    btn.setProperty("speakOn", "true" if on else "false")
    st = btn.style()
    if st is not None:
        st.unpolish(btn)
        st.polish(btn)
    btn.update()


def make_speak_button(label):
    btn = apply_std_button(QPushButton(label))
    btn.setMinimumWidth(0)
    btn.setToolTip(_TIP)
    style_speak_button(btn, False)
    return btn


class _EdtPlay:
    """与阅读器 _WavPlay 相同的暂停逻辑，只用独立 MCI 别名。"""

    def __init__(self):
        self.open = False
        self.held = False
        self._lock = threading.Lock()
        self._seen = False
        self._t0 = 0.0
        self._ms = 0.0

    def _mci(self, cmd):
        _ensure_reader_path()
        from cosy_speak import _mci
        return _mci(cmd)

    def start(self, path):
        with self._lock:
            self._close_locked()
            # 停/切语音会在界面线程 close 别名，再在播放线程 open；残留时重试一次。
            self._mci("close %s" % _ALIAS)
            err, _msg = self._mci('open "%s" type waveaudio alias %s' % (path, _ALIAS))
            if err:
                self._mci("close %s" % _ALIAS)
                time.sleep(0.05)
                err, _msg = self._mci('open "%s" type waveaudio alias %s' % (path, _ALIAS))
            if err:
                raise RuntimeError("这段声音播不出来")
            self.open = True
            self.held = False
            self._seen = False
            self._t0 = time.monotonic()
            _err, length = self._mci("status %s length" % _ALIAS)
            try:
                self._ms = float(length or 0)
            except (TypeError, ValueError):
                self._ms = 0.0
            err, _msg = self._mci("play %s" % _ALIAS)
            if err:
                self._close_locked()
                raise RuntimeError("这段声音播不出来")

    def _mode(self):
        _err, mode = self._mci("status %s mode" % _ALIAS)
        return (mode or "").strip().lower()

    def sync(self, paused):
        with self._lock:
            if not self.open:
                return
            if paused:
                mode = self._mode()
                if mode == "playing" or not self.held:
                    self._mci("pause %s" % _ALIAS)
                self.held = True
            else:
                # 暂停后若 resume 一次没落下，_wait_play 里会反复 sync(False)；
                # 只要 MCI 仍停在 paused，就再试一次，避免第一段播完后卡住。
                if self.held or self._mode() == "paused":
                    self._mci("resume %s" % _ALIAS)
                self.held = False

    def finished(self):
        with self._lock:
            if not self.open:
                return True
            if self.held:
                return False
            mode = self._mode()
            if mode in ("playing", "paused", "seeking"):
                self._seen = True
                return False
            if mode == "stopped" and self._seen:
                return True
            # 空/未知模式：刚 open 时 MCI 可能还没进入 playing，不能当成播完，
            # 否则第一段立刻结束，后面的句子不再开播。
            if self._ms > 0:
                return (time.monotonic() - self._t0) * 1000.0 >= self._ms + 200.0
            return bool(self._seen)

    def progress(self):
        with self._lock:
            if not self.open:
                return None
            _err, pos = self._mci("status %s position" % _ALIAS)
            _err2, length = self._mci("status %s length" % _ALIAS)
        try:
            p, total = float(pos or 0), float(length or 0)
        except (TypeError, ValueError):
            return None
        if total <= 0:
            return None
        return max(0.0, min(1.0, p / total))

    def close(self):
        with self._lock:
            self._close_locked()

    def _close_locked(self):
        if self.open:
            self._mci("stop %s" % _ALIAS)
            self._mci("close %s" % _ALIAS)
        self.open = False
        self.held = False
        self._seen = False
        self._ms = 0.0


class EditorSpeak(QObject):
    """在界面线程收 CosyReader 事件；合成和播放都在后台。"""

    changed = pyqtSignal()
    failed = pyqtSignal(str)
    _bus = pyqtSignal(int, str, str)

    def __init__(self, parent=None, synth=None, player=None):
        super().__init__(parent)
        self.voice = "男"
        self.on = False
        self.paused = False
        self._gen = 0
        self._reader = None
        self._synth = synth
        self._player = player
        self._bus.connect(self._on_bus, Qt.QueuedConnection)

    def _emit_worker(self, gen, kind, text):
        self._bus.emit(int(gen), str(kind or ""), text or "")

    def _ensure(self):
        if self._reader is not None:
            return self._reader
        root = _ensure_reader_path()
        from cosy_speak import CosyReader
        player = self._player if self._player is not None else _EdtPlay()
        self._reader = CosyReader(root, self._emit_worker, synth=self._synth, player=player)
        return self._reader

    def start(self, text, voice):
        text = speakable_plain(text)
        if not text:
            self.failed.emit("没有可朗读的文字")
            return False
        voice = voice if voice in ("男", "女") else "男"
        root = _ensure_reader_path()
        from cosy_speak import resolve_voice
        cfg = resolve_voice(root, voice)
        if not cfg.get("ok"):
            self.failed.emit(cfg.get("error") or "无法朗读")
            return False
        if self.on:
            self.stop(silent=True)
        self.voice = voice
        self._gen += 1
        self.on = True
        self.paused = False
        try:
            self._ensure().start(text, self._gen, cfg)
        except Exception as exc:
            self.on = False
            self.paused = False
            from cosy_speak import brief_error
            self.failed.emit(brief_error(str(exc)))
            self.changed.emit()
            return False
        self.changed.emit()
        return True

    def toggle(self, voice, text):
        if voice not in ("男", "女"):
            return False
        if self.on and self.voice == voice:
            reader = self._reader
            if reader is None:
                return False
            if self.paused:
                reader.resume()
                self.paused = False
            else:
                player = getattr(reader, "_play", None)
                # 段与段之间播放器已 close（下一段还在合成）。此时再点同一按钮
                # 会把 CosyReader._pause 卡住，后面的句子永远等 resume。
                if player is not None and not getattr(player, "open", False):
                    return True
                reader.pause()
                self.paused = True
            self.changed.emit()
            return True
        return self.start(text, voice)

    def stop(self, silent=False):
        self._gen += 1
        was = self.on
        self.on = False
        self.paused = False
        reader = self._reader
        if reader is not None:
            try:
                reader.stop()
            except Exception:
                pass
        if was or not silent:
            self.changed.emit()

    def close(self):
        self.stop(silent=True)
        reader = self._reader
        self._reader = None
        if reader is not None:
            try:
                reader.close()
            except Exception:
                pass
        self.changed.emit()

    def _on_bus(self, gen, kind, text):
        if int(gen) != int(self._gen):
            return
        if kind == "play":
            self.on = True
            self.changed.emit()
        elif kind == "done":
            self.on = False
            self.paused = False
            self.changed.emit()
        elif kind == "error":
            self.on = False
            self.paused = False
            self.failed.emit(text or "语音合成失败")
            self.changed.emit()