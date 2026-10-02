# utils/clock_idle.py
# ---------------------------------------------------------------------------
# 电子钟空闲跳转 + 助手页按住不切。
#
# 时长只按左侧菜单分类（不再按单个业务页分别设）：
#   第一类 pinned（无分区名 / 置顶常用）→ 5 分钟
#   第二类 assistant（助手 · 主手脚）    → 10 分钟
#   第三类 tool（工具 · 小偏门）         → 3 分钟
#   系统总览 overview                   → 3 分钟（底栏，不在三类里）
#
#   HOLD_PAGE_SECTIONS   这些分区：批处理 / 预下载等后台任务不要把当前页切走
# ---------------------------------------------------------------------------
from __future__ import annotations

from PyQt5.QtCore import QEvent, QObject, QTimer
from PyQt5.QtWidgets import QApplication

from utils.logger import get_logger

log = get_logger(__name__)

# 兜底（未知页）：按工具类 3 分钟
DEFAULT_IDLE_MS = 3 * 60 * 1000
PINNED_IDLE_MS = 5 * 60 * 1000
ASSISTANT_IDLE_MS = 10 * 60 * 1000
TOOL_IDLE_MS = 3 * 60 * 1000
OVERVIEW_IDLE_MS = 3 * 60 * 1000
GUARD_INTERVAL_MS = 5000

# 临时关闭：空闲不再自动跳电子钟（排查展示图轮播被打断等问题时用）
IDLE_JUMP_ENABLED = False

# 侧栏分区 → 空闲毫秒。分区 key 与 ui_main._SIDE_SECTION_MAP 一致。
SECTION_IDLE_MS = {
    "pinned": PINNED_IDLE_MS,          # 第一类（无名字）
    "assistant": ASSISTANT_IDLE_MS,    # 第二类 助手 · 主手脚
    "tool": TOOL_IDLE_MS,              # 第三类 工具 · 小偏门
}

# 单页覆盖：仅系统总览（底栏，不在三类侧栏分区里）
PAGE_IDLE_MS = {
    "overview": OVERVIEW_IDLE_MS,
}

# 侧栏分区：后台自动切页（预下载开跑、批处理混链切视频/图集、下完回速存、热键切截图/录屏）时按住。
# 含「游戏会议」等助手页：在本页时其它功能不得自动跳走。用户自己点侧栏 / 顶栏仍可离开。
# 电子钟空闲跳转不走这条（且 IDLE_JUMP_ENABLED 现为 False）。
HOLD_PAGE_SECTIONS = frozenset({"assistant"})  # 助手 · 主手脚

# 导航 key → MainWindow 上的页面属性名（用来判断当前在哪一页）
_PAGE_ATTR = {
    "overview": "page_overview",
    "fast": "page_fast",
    "video": "page_douyin",
    "audio": "page_audio",
    "gallery": "page_gallery",
    "paste": "page_paste",
    "img_proc": "page_img_proc",
    "voice": "page_voice",
    "auk": "page_auk",
    "text_edit": "page_text_editor",
    "gameassist": "page_gameassist",
    "gamebrain": "page_gamebrain",
    "gamemeet": "page_gamemeet",
    "prompt": "page_prompt",
    "points": "page_points",
    "shot": "page_shot",
    "region_rec": "page_region_rec",
    "ratio": "page_ratio",
    "dir_link": "page_dir_link",
    "tz_fx": "page_tz_fx",
}


def idle_ms_for(page_key: str | None, section_of_key: dict | None) -> int:
    """当前页该等多久再进电子钟。"""
    key = page_key or ""
    if key in PAGE_IDLE_MS:
        try:
            return int(PAGE_IDLE_MS[key])
        except (TypeError, ValueError):
            pass
    sec = (section_of_key or {}).get(key)
    if sec in SECTION_IDLE_MS:
        try:
            return int(SECTION_IDLE_MS[sec])
        except (TypeError, ValueError):
            pass
    return int(DEFAULT_IDLE_MS)


def should_hold_page(page_key: str | None, section_of_key: dict | None) -> bool:
    """当前页是否属于「按住不切」分区。"""
    key = page_key or ""
    sec = (section_of_key or {}).get(key)
    return sec in HOLD_PAGE_SECTIONS


class _IdleWatcher(QObject):
    """捕获键鼠活动，重置空闲计时。"""

    _WATCH = frozenset((
        QEvent.MouseMove,
        QEvent.MouseButtonPress,
        QEvent.MouseButtonDblClick,
        QEvent.KeyPress,
        QEvent.Wheel,
    ))

    def __init__(self, ctrl: "ClockIdleController"):
        super().__init__(ctrl._host)
        self._ctrl = ctrl

    def eventFilter(self, obj, event):
        try:
            if event.type() in self._WATCH:
                self._ctrl.note_activity()
        except Exception:
            pass
        return False


class ClockIdleController:
    """主窗口挂上这一份即可：计时、挡任务、空闲进电子钟。"""

    def __init__(self, host, *, section_of_key: dict):
        self._host = host
        self._section_of_key = dict(section_of_key or {})
        self._timer = QTimer(host)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEFAULT_IDLE_MS)
        self._timer.timeout.connect(self._on_timeout)
        self._guard = QTimer(host)
        self._guard.setInterval(GUARD_INTERVAL_MS)
        self._guard.timeout.connect(self._guard_tick)
        self._watcher = _IdleWatcher(self)

    @classmethod
    def install(cls, host, *, section_of_key: dict) -> "ClockIdleController":
        ctrl = cls(host, section_of_key=section_of_key)
        ctrl._guard.start()
        try:
            app = QApplication.instance()
            if app is not None:
                app.installEventFilter(ctrl._watcher)
        except Exception:
            log.exception("安装电子钟空闲监听失败")
        ctrl.arm()
        return ctrl

    def shutdown(self) -> None:
        self.stop()
        try:
            self._guard.stop()
        except Exception:
            pass
        try:
            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(self._watcher)
        except Exception:
            pass

    def stop(self) -> None:
        try:
            self._timer.stop()
        except Exception:
            pass

    def arm(self) -> None:
        """无任务且不在电子钟页时，按当前页重新计空闲。"""
        if not IDLE_JUMP_ENABLED:
            self.stop()
            return
        if self._on_clock() or self._blocked_by_task():
            self.stop()
            return
        self._timer.setInterval(self.interval_ms())
        self._timer.start()

    def note_activity(self) -> None:
        if self._on_clock():
            return
        self.arm()

    def interval_ms(self) -> int:
        return idle_ms_for(self._current_page_key(), self._section_of_key)

    def should_hold_page(self) -> bool:
        return should_hold_page(self._current_page_key(), self._section_of_key)

    def _current_page_key(self) -> str | None:
        host = self._host
        stack = getattr(host, "stack", None)
        if stack is None:
            return None
        try:
            w = stack.currentWidget()
        except Exception:
            return None
        if w is None:
            return None
        for key, attr in _PAGE_ATTR.items():
            if getattr(host, attr, None) is w:
                return key
        return None

    def _on_clock(self) -> bool:
        host = self._host
        page = getattr(host, "page_clock", None)
        stack = getattr(host, "stack", None)
        if page is None or stack is None:
            return False
        try:
            return stack.currentWidget() is page
        except Exception:
            return False

    def _on_timeout(self) -> None:
        if not IDLE_JUMP_ENABLED:
            self.stop()
            return
        if self._on_clock() or self._blocked_by_task():
            return
        fn = getattr(self._host, "_goto_clock_page", None)
        if callable(fn):
            fn()

    def _guard_tick(self) -> None:
        """任务结束后补上计时；任务进行中停表。"""
        if not IDLE_JUMP_ENABLED:
            self.stop()
            return
        if self._on_clock():
            return
        if self._blocked_by_task():
            self.stop()
            return
        if not self._timer.isActive():
            self._timer.setInterval(self.interval_ms())
            self._timer.start()

    def _blocked_by_task(self) -> bool:
        """有下载 / 录屏 / 语音任务，积分记录正在编辑，或弹窗未关时，不自动进电子钟。

        弹窗未关时停表：避免用户在看错误/提示弹窗期间，主窗口被切到电子钟屏保。
        弹窗关闭后，guard 会在数秒内重新从满额开始计时。
        """
        host = self._host
        try:
            if QApplication.activeModalWidget() is not None:
                return True
        except Exception:
            pass
        try:
            if QApplication.activePopupWidget() is not None:
                return True
        except Exception:
            pass
        try:
            if host._is_any_download_busy():
                return True
        except Exception:
            pass
        rec = getattr(host, "page_region_rec", None)
        if rec is not None:
            recorder = getattr(rec, "_recorder", None)
            if recorder is not None and getattr(recorder, "is_recording", False):
                return True
        voice = getattr(host, "page_voice", None)
        if voice is not None and getattr(voice, "_state", "") in ("recording", "transcribing"):
            return True
        # 语音克隆已拆到「音频处理」页的 CosVoice3 页签
        clone = getattr(getattr(host, "page_auk", None), "page_cosvoice3", None)
        if clone is not None and getattr(clone, "_state", "") == "synth":
            return True
        game = getattr(host, "page_gameassist", None)
        if game is not None and getattr(game, "_state", "") == "busy":
            return True
        brain = getattr(host, "page_gamebrain", None)
        if brain is not None and getattr(brain, "_state", "") == "busy":
            return True
        fast = getattr(host, "page_fast", None)
        if fast is not None and getattr(fast, "_recording", False):
            return True
        points = getattr(host, "page_points", None)
        if points is not None and getattr(points, "_editing_id", None):
            return True
        return False
