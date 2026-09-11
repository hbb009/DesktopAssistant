# ==================== 标准库 ====================
import os
import re
import shutil
import base64
import hashlib
import subprocess
import threading
import time
import urllib.request

# ==================== 第三方：PyQt5 ====================
from PyQt5.QtCore import (
    Qt, QStandardPaths, QTimer, pyqtSignal, QRectF,
    QAbstractNativeEventFilter, QCoreApplication, QSize,
    QObject, QEvent,
)
from PyQt5.QtGui import (
    QKeySequence, QIcon, QPixmap, QPainter, QColor, QPen, QBrush, QPainterPath, QFont,
)
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QListWidget,
    QCheckBox, QFileDialog, QShortcut,
    QComboBox, QTabWidget, QStackedWidget,
    QRadioButton, QButtonGroup, QScrollArea,
    QFrame, QSizePolicy, QListWidgetItem, QAbstractItemView,
)

# ==================== 本地模块 ====================
from styles.style_all import (
    install_card_title,
    make_card,
    apply_folder_path_edit,
    restyle_folder_path_edit,
    apply_medium_button,
    apply_simple_record,
    make_glyph_icon,
    restyle_card_frame,
    restyle_card_title,
    content_secondary_color,
    message_box_info, message_box_warn,
    CARD_TOP_GAP,
    CARD_LEFT_GAP,
    CARD_RIGHT_GAP,
    CARD_BOTTOM_GAP,
    theme,
    tk,
)
from utils.cursor_toast import (
    show_cursor_toast, start_record_bubble, stop_record_bubble,
    update_record_bubble_count,
)

# ==================== Windows API ====================
import ctypes
import ctypes.wintypes as wintypes
import socket

try:
    LRESULT = wintypes.LRESULT
except AttributeError:
    LRESULT = ctypes.c_long if ctypes.sizeof(ctypes.c_void_p) == 4 else ctypes.c_longlong
try:
    INT = wintypes.INT
except AttributeError:
    INT = ctypes.c_int
try:
    WPARAM = wintypes.WPARAM
    LPARAM = wintypes.LPARAM
except AttributeError:
    WPARAM = ctypes.c_size_t
    LPARAM = ctypes.c_ssize_t
try:
    ULONG_PTR = wintypes.ULONG_PTR
except AttributeError:
    ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

if ctypes.sizeof(WPARAM) != ctypes.sizeof(ctypes.c_void_p):
    WPARAM = ctypes.c_size_t
if ctypes.sizeof(LPARAM) != ctypes.sizeof(ctypes.c_void_p):
    LPARAM = ctypes.c_ssize_t

WM_HOTKEY = 0x0312
VK_CODE   = {"F3": 0x72, "F4": 0x73, "F5": 0x74, "F6": 0x75, "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79, "F12": 0x7B}
# 缩放重启会先拉起新进程再退出旧进程；旧进程还占着热键时 RegisterHotKey 会失败。
# 失败后按这个间隔再试几次，等旧进程放掉。
_HK_RETRY_MS = (300, 700, 1500, 3000)
_ERROR_HOTKEY_ALREADY_REGISTERED = 1409
try:
    _USER32 = ctypes.WinDLL("user32", use_last_error=True)
except Exception:  # pragma: no cover
    _USER32 = ctypes.windll.user32


def _win_register_hotkey(hotkey_id: int, vk: int, mods: int = 0):
    """RegisterHotKey(NULL, …)。返回 (ok, winerr)。"""
    try:
        ok = bool(_USER32.RegisterHotKey(None, int(hotkey_id), int(mods), int(vk)))
    except Exception:
        return False, -1
    if ok:
        return True, 0
    try:
        return False, int(ctypes.get_last_error() or 0)
    except Exception:
        return False, 0


def _win_unregister_hotkey(hotkey_id: int) -> None:
    try:
        _USER32.UnregisterHotKey(None, int(hotkey_id))
    except Exception:
        pass

# 手动速存依赖（探测 win32 剪贴板能力 + pyperclip）
try:
    import win32clipboard  # noqa: F401  # 能力探测
    import win32gui
    import win32com.client
    import pyperclip
    _WIN_OK = True
except Exception:
    win32clipboard = None
    win32gui = None
    win32com = None
    pyperclip = None
    _WIN_OK = False

_SHARED_QUEUE = []

# ── WebSocket Server（供浏览器扩展连接）────────────────────────
# 生命周期：由 start_ws_server() / stop_ws_server() 显式管理，
# 禁止在 import 时起线程（避免测试/IDE 误 import 占端口）。
from utils.logger import get_logger as _get_logger
from utils.flow_layout import FlowLayout
_ws_log = _get_logger("pages.page_fast_save.ws")
log = _get_logger(__name__)

_WS_PORT        = 19876
# MV3 Service Worker 休眠后可能 30–60s 才补心跳；TTL 过短会误判断连
_WS_PING_TTL    = 90.0
_WS_MAX_PAYLOAD = 64 * 1024      # 单帧上限，防恶意大包把线程堵死
_WS_CLIENT_TIMEOUT = 75.0        # 单连接 recv 超时，清掉半死不活的 socket
_ws_clients     = set()          # 当前 socket 连接集合
_ws_lock        = threading.Lock()
_ws_last_ping   = 0.0            # 最后一次收到插件 PING 的时间戳
_ws_send_lock   = threading.Lock()   # 多线程往同一 socket 写时加锁，避免帧交错
_ws_enabled     = False          # 桌面程序侧“启用速存图片”的状态，会推送给插件
_ws_stop        = threading.Event()
_ws_srv_sock    = None           # type: ignore
_ws_started     = False
_ws_pusher_alive = False
_ws_start_lock  = threading.Lock()
# 服务器启动状态：None=未启动/启动中, True=正常, False=端口被占用/失败
_ws_server_ok   = None

# ── SAVE 下载目标目录（由页面初始化时注入，直接存用户路径，不经过暂存区）──
_ws_user_save_dir = ""

# ── WS SAVE 事件回调（由 PageFastSave 实例在 __init__ 中注册）──
# cb_start(url)     — 收到插件 SAVE 命令时调用（任意线程）
# cb_done(url, filename, success) — 下载完成时调用（任意线程）
_ws_save_started_cb = None
_ws_save_done_cb = None


def _ws_set_save_dir(path: str):
    global _ws_user_save_dir
    _ws_user_save_dir = path.strip()


def _ws_parse_headers(raw: str) -> dict:
    headers = {}
    for line in (raw or "").split("\r\n")[1:]:
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        headers[k.strip().lower()] = v.strip()
    return headers


def _ws_origin_allowed(origin: str) -> bool:
    """仅允许本机扩展或无 Origin（部分原生客户端）。

    浏览器打开的任意 https 页面也能连 127.0.0.1 的 WebSocket（同源策略不管 WS），
    因此必须拒绝 http(s):// 的 Origin，只放行 chrome-extension / moz-extension。
    """
    o = (origin or "").strip()
    if not o:
        # 扩展一般会带 Origin；空 Origin 留给本机调试/非浏览器客户端
        return True
    low = o.lower()
    if low.startswith("chrome-extension://") or low.startswith("moz-extension://"):
        return True
    # 明确拒绝网页 Origin（含 null 字符串）
    _ws_log.warning("WS 握手拒绝 Origin=%s", o[:120])
    return False


def _ws_handshake(conn) -> bool:
    try:
        data = conn.recv(4096).decode("utf-8", errors="ignore")
    except Exception:
        return False
    if not data:
        return False
    headers = _ws_parse_headers(data)
    if not _ws_origin_allowed(headers.get("origin", "")):
        try:
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
        except Exception:
            pass
        return False
    key = headers.get("sec-websocket-key")
    if not key:
        return False
    accept = base64.b64encode(
        hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
    ).decode()
    try:
        conn.sendall((
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
        ).encode())
    except Exception:
        return False
    return True


def _ws_send_raw(conn, opcode: int, payload: bytes = b""):
    """发一帧服务端→客户端（不掩码）。opcode: 1=text 8=close 9=ping 10=pong。"""
    p = payload if isinstance(payload, (bytes, bytearray)) else bytes(payload or b"")
    n = len(p)
    if n < 126:
        h = bytes([0x80 | (opcode & 0x0F), n])
    elif n < 65536:
        h = bytes([0x80 | (opcode & 0x0F), 126, (n >> 8) & 0xFF, n & 0xFF])
    else:
        # 本协议从不发大包
        raise ValueError("WS payload too large")
    conn.sendall(h + p)


def _ws_send(conn, text):
    p = (text or "").encode("utf-8")
    n = len(p)
    if n < 126:
        h = bytes([0x81, n])
    elif n < 65536:
        h = bytes([0x81, 126, (n >> 8) & 0xFF, n & 0xFF])
    else:
        _ws_log.warning("WS 文本过长 n=%s，截断发送", n)
        p = p[:65000]
        n = len(p)
        h = bytes([0x81, 126, (n >> 8) & 0xFF, n & 0xFF])
    conn.sendall(h + p)


def _ws_recv_exact(conn, n: int) -> bytes:
    """读满 n 字节；对端关闭返回不足；超时抛 socket.timeout。"""
    data = bytearray()
    while len(data) < n:
        chunk = conn.recv(n - len(data))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


def _ws_recv(conn):
    """读一帧。返回 str 文本；\"\" 表示控制帧已处理可继续；None 表示应断开。"""
    try:
        h = _ws_recv_exact(conn, 2)
        if len(h) < 2:
            return None
        opcode = h[0] & 0x0F
        masked = h[1] & 0x80
        n = h[1] & 0x7F
        if n == 126:
            ext = _ws_recv_exact(conn, 2)
            if len(ext) < 2:
                return None
            n = int.from_bytes(ext, "big")
        elif n == 127:
            # 不接受 64 位超大帧
            return None
        if n > _WS_MAX_PAYLOAD:
            _ws_log.warning("WS 帧过大 n=%s，断开", n)
            return None
        mask = b"\x00\x00\x00\x00"
        if masked:
            mask = _ws_recv_exact(conn, 4)
            if len(mask) < 4:
                return None
        data = bytearray(_ws_recv_exact(conn, n)) if n else bytearray()
        if n and len(data) < n:
            return None
        if masked and data:
            for i in range(len(data)):
                data[i] ^= mask[i % 4]
        # 0x8 close → 断开；0x9 ping → 回 pong；0xA pong → 忽略
        if opcode == 0x8:
            try:
                _ws_send_raw(conn, 0x8, bytes(data[:2]) if len(data) >= 2 else b"")
            except Exception:
                pass
            return None
        if opcode == 0x9:
            try:
                with _ws_send_lock:
                    _ws_send_raw(conn, 0xA, bytes(data))
            except Exception:
                return None
            return ""
        if opcode == 0xA:
            return ""
        if opcode in (1, 2):
            return data.decode("utf-8", errors="ignore")
        # 未知 opcode：忽略
        return ""
    except socket.timeout:
        # 交给上层区分「空闲超时」与「对端断开」
        raise
    except Exception:
        return None


# ── 浏览器插件 SAVE 请求：用 curl 下载（解决 pixiv 等 CDN 需要 Referer 的问题）──
_SAVE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _ws_staging_dir() -> str:
    dl = os.path.join(os.path.expanduser("~"), "Downloads")
    return os.path.join(dl, "WebImageSaver")


def _ws_download_image(url: str):
    """用 curl 下载图片直接到用户保存路径（与图集下载 Pixiv 逻辑一致）。
    扩展通过 WS 发来 SAVE URL，此函数负责下载，文件名从 URL 提取。
    """
    target_dir = _ws_user_save_dir or _ws_staging_dir()
    os.makedirs(target_dir, exist_ok=True)

    # 从 URL 提取文件名（如 https://i.pximg.net/.../xxx_p0.jpg → xxx_p0.jpg）
    from urllib.parse import urlparse, unquote
    path = urlparse(url).path
    name = unquote(os.path.basename(path)) if path else ""
    # 清洗非法字符
    name = re.sub(r'[\\/:*?"<>|]+', '_', name or "image").strip().strip(".")
    if not re.search(r'\.(png|jpe?g|webp|gif|bmp|avif|tiff?|ico)$', name, re.I):
        name += ".jpg"
    # 去重
    dest = os.path.join(target_dir, name)
    base, ext = os.path.splitext(dest)
    i = 1
    while os.path.exists(dest):
        dest = f"{base} ({i}){ext}"
        i += 1
    tmp = dest + ".part"

    # Referer：部分 CDN 校验来源。pixiv / DeviantArt(wixmp) 不带会 403。
    low = (url or "").lower()
    if "pximg.net" in low:
        referer = "https://www.pixiv.net/"
    elif "wixmp.com" in low or "deviantart.net" in low or "deviantart.com" in low:
        referer = "https://www.deviantart.com/"
    else:
        referer = ""

    # 系统代理（与图集下载 _system_proxy_for_curl 一致）
    proxy_url = ""
    try:
        proxies = urllib.request.getproxies() or {}
        proxy_url = proxies.get("https") or proxies.get("http") or ""
    except Exception:
        pass

    import shutil
    curl_exe = shutil.which("curl") or shutil.which("curl.exe") or "curl"
    cmd = [curl_exe, "-L", "-o", tmp,
           "-H", f"User-Agent: {_SAVE_UA}",
           "-H", "Accept: image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
           "--connect-timeout", "20", "--max-time", "120",
           "-w", "\n%{http_code}", "-sS"]
    if referer:
        cmd += ["-H", f"Referer: {referer}"]
    if proxy_url:
        cmd += ["-x", proxy_url]
    cmd.append(url)

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=130,
                              creationflags=subprocess.CREATE_NO_WINDOW)
        http_code = ""
        if proc.stdout:
            lines = proc.stdout.strip().splitlines()
            if lines:
                http_code = lines[-1].strip()
        if http_code and http_code.isdigit() and int(http_code) >= 400:
            if os.path.isfile(tmp):
                os.remove(tmp)
            _ws_log.warning("curl HTTP %s %s", http_code, url[:120])
            return (False, "")
        if proc.returncode != 0 or not os.path.isfile(tmp):
            _ws_log.warning("curl 失败(%d) %s", proc.returncode, url[:120])
            return (False, "")
        if os.path.getsize(tmp) < 64:
            os.remove(tmp)
            _ws_log.warning("curl 文件过小 %s", url[:120])
            return (False, "")
        os.replace(tmp, dest)
        _ws_log.info("SAVE 已下载 %s", os.path.basename(dest))
        return (True, dest)
    except Exception:
        _ws_log.exception("SAVE curl 异常 %s", url[:120])
        if os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
        return (False, "")


def _ws_client(conn):
    global _ws_last_ping
    try:
        try:
            conn.settimeout(_WS_CLIENT_TIMEOUT)
        except Exception:
            pass
        if not _ws_handshake(conn):
            return
        with _ws_lock:
            _ws_clients.add(conn)
            _ws_last_ping = time.time()
        with _ws_send_lock:
            try:
                _ws_send(conn, "ENABLE" if _ws_enabled else "DISABLE")
            except Exception:
                pass
        while not _ws_stop.is_set():
            try:
                msg = _ws_recv(conn)
            except socket.timeout:
                # 长时间无帧：可能 SW 已休眠；保留连接等下一次 PING，不立刻踢
                with _ws_lock:
                    stale = (time.time() - _ws_last_ping) > _WS_PING_TTL
                if stale:
                    break
                continue
            if msg is None:
                break
            if msg == "PING":
                with _ws_lock:
                    _ws_last_ping = time.time()
                # 回 PONG：插件可确认链路双向可用（旧插件忽略即可）
                with _ws_send_lock:
                    try:
                        _ws_send(conn, "PONG")
                    except Exception:
                        break
            elif msg.startswith("SAVE "):
                url = msg[5:].strip()
                if url:
                    cb_start = _ws_save_started_cb
                    cb_done  = _ws_save_done_cb
                    def _do_save():
                        if cb_start is not None:
                            try:
                                cb_start(url)
                            except Exception:
                                _ws_log.exception("SAVE 开始回调失败 %s", url[:120])
                        ok, dest_path = _ws_download_image(url)
                        # 回调第二参传完整本地路径（失败则为 ""），供运行记录预览用
                        if cb_done is not None:
                            try:
                                cb_done(url, dest_path if ok and dest_path else "", ok)
                            except Exception:
                                _ws_log.exception(
                                    "SAVE 完成回调失败 ok=%s %s", ok, url[:120]
                                )
                    threading.Thread(
                        target=_do_save,
                        daemon=True,
                    ).start()
    except Exception:
        _ws_log.exception("WS 客户端会话异常")
    finally:
        with _ws_lock:
            _ws_clients.discard(conn)
        try:
            conn.close()
        except Exception:
            pass


def _ws_schedule_restart(delay: float = 2.0):
    """监听线程意外退出后延迟重启（关窗 stop 时不调度）。"""
    def _go():
        if _ws_stop.is_set():
            return
        _ws_log.info("正在自动重启 WS 服务…")
        start_ws_server()

    t = threading.Timer(max(0.5, float(delay)), _go)
    t.daemon = True
    t.name = "ws-restart"
    t.start()


def _ws_server():
    global _ws_server_ok, _ws_srv_sock, _ws_started
    srv = None
    intentional_stop = False
    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Windows：进程异常退出后尽快释放端口，方便自愈重启
        try:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 0)
        except Exception:
            pass
        srv.bind(("127.0.0.1", _WS_PORT))
        srv.listen(10)
        srv.settimeout(1.0)
        _ws_srv_sock = srv
        _ws_server_ok = True
        _ws_log.info("WS 服务已监听 127.0.0.1:%s", _WS_PORT)
        while not _ws_stop.is_set():
            try:
                conn, addr = srv.accept()
                # 双保险：只处理本机连接
                if addr and addr[0] not in ("127.0.0.1", "::1"):
                    try:
                        conn.close()
                    except Exception:
                        pass
                    continue
                threading.Thread(
                    target=_ws_client, args=(conn,), daemon=True, name="ws-client"
                ).start()
            except socket.timeout:
                continue
            except Exception:
                if _ws_stop.is_set():
                    intentional_stop = True
                    break
                _ws_log.exception("WS accept 异常")
                break
        intentional_stop = _ws_stop.is_set()
    except OSError as e:
        # 常见：端口被占用 → 标红，稍后仍可再试（用户关掉占用进程后自愈）
        _ws_log.exception("WS 服务启动失败 port=%s err=%s", _WS_PORT, e)
        _ws_server_ok = False
        intentional_stop = _ws_stop.is_set()
    except Exception:
        _ws_log.exception("WS 服务启动失败 port=%s", _WS_PORT)
        _ws_server_ok = False
        intentional_stop = _ws_stop.is_set()
    finally:
        _ws_srv_sock = None
        if srv is not None:
            try:
                srv.close()
            except Exception:
                pass
        with _ws_start_lock:
            _ws_started = False
        _ws_log.info("WS 服务线程退出 intentional=%s", intentional_stop)
        # 非主动停止：自动重启（端口占用也会隔一段时间再试）
        if not intentional_stop and not _ws_stop.is_set():
            _ws_server_ok = False
            _ws_schedule_restart(3.0)


def _ws_broadcast(text):
    """把一条消息发给所有已连接的插件（线程安全）。失败则踢掉死连接。"""
    with _ws_lock:
        clients = list(_ws_clients)
    dead = []
    for c in clients:
        with _ws_send_lock:
            try:
                _ws_send(c, text)
            except Exception:
                dead.append(c)
    if dead:
        with _ws_lock:
            for c in dead:
                _ws_clients.discard(c)
        for c in dead:
            try:
                c.close()
            except Exception:
                pass


def _ws_set_enabled(enabled: bool):
    """更新启用状态并立即推送给插件。"""
    global _ws_enabled
    _ws_enabled = bool(enabled)
    _ws_broadcast("ENABLE" if _ws_enabled else "DISABLE")


def _ws_connected():
    """判断插件是否活跃：TTL 内收到过 PING，或 socket 仍在。"""
    with _ws_lock:
        has_socket = len(_ws_clients) > 0
        ping_fresh = (time.time() - _ws_last_ping) < _WS_PING_TTL
    return has_socket and ping_fresh


def _ws_seconds_since_ping() -> float:
    with _ws_lock:
        return time.time() - _ws_last_ping


def _ws_state_pusher():
    """每 10 秒重发一次当前启用状态。插件据此判断“程序是否在线”。"""
    global _ws_pusher_alive
    try:
        while not _ws_stop.wait(10.0):
            try:
                _ws_broadcast("ENABLE" if _ws_enabled else "DISABLE")
            except Exception:
                _ws_log.exception("WS 状态推送失败")
    finally:
        _ws_pusher_alive = False


def start_ws_server():
    """显式启动 WS（PageFastSave 构造 / 自愈重启）。幂等。"""
    global _ws_started, _ws_server_ok, _ws_pusher_alive
    with _ws_start_lock:
        if _ws_started:
            return
        _ws_started = True
    _ws_stop.clear()
    _ws_server_ok = None
    threading.Thread(target=_ws_server, daemon=True, name="ws-server").start()
    if not _ws_pusher_alive:
        _ws_pusher_alive = True
        threading.Thread(target=_ws_state_pusher, daemon=True, name="ws-pusher").start()
    _ws_log.info("已请求启动 WS 服务")


def ensure_ws_server():
    """状态灯检测时调用：若监听线程已死则拉起（不重复起）。"""
    if _ws_stop.is_set():
        return
    if _ws_started and _ws_server_ok is True:
        return
    if _ws_started and _ws_server_ok is None:
        return  # 启动中
    # 未启动，或曾失败且线程已退出
    if not _ws_started:
        start_ws_server()


def stop_ws_server():
    """关窗时停止监听并断开客户端。幂等。"""
    global _ws_started, _ws_server_ok, _ws_srv_sock
    _ws_stop.set()
    sock = _ws_srv_sock
    _ws_srv_sock = None
    if sock is not None:
        try:
            sock.close()
        except Exception:
            pass
    with _ws_lock:
        clients = list(_ws_clients)
        _ws_clients.clear()
    for c in clients:
        try:
            c.close()
        except Exception:
            pass
    with _ws_start_lock:
        _ws_started = False
    _ws_server_ok = None
    _ws_log.info("已停止 WS 服务")






# ─────────────────────────────────────────────
# 全局热键过滤器（速存文本用）
# ─────────────────────────────────────────────
class _GlobalHotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, cb, hid):
        super().__init__()
        self.cb  = cb
        self.hid = hid

    def nativeEventFilter(self, eventType, message):
        if eventType != "windows_generic_MSG":
            return False, 0
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == WM_HOTKEY and msg.wParam == self.hid:
            self.cb()
            return True, 0
        return False, 0


def _check_chrome_running() -> bool:
    """检测系统中是否有 Chrome 进程在运行"""
    try:
        import subprocess
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/NH"],
            stderr=subprocess.DEVNULL, creationflags=0x08000000  # CREATE_NO_WINDOW
        ).decode("gbk", errors="ignore")
        return "chrome.exe" in out.lower()
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════
# 记录芯片：按「文字 + 左右各 1 汉字」紧算宽，避免全局 QPushButton
# padding(14px) / sizeHint 把卡撑得过宽；FlowLayout 也吃 sizeHint。
# ═══════════════════════════════════════════════════════════════
class _RecordCardChip(QPushButton):
    """速存文本 · 记录文件芯片（cards/*.txt）。"""

    _H = 28
    _FONT_PX = 13

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        f = QFont(self.font())
        f.setPixelSize(self._FONT_PX)
        f.setWeight(QFont.Normal)
        self.setFont(f)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self._recalc_width()

    def _side_pad(self) -> int:
        """左右各留 1 个汉字宽。"""
        try:
            return max(1, self.fontMetrics().horizontalAdvance("字"))
        except Exception:
            return self._FONT_PX

    def _recalc_width(self) -> int:
        fm = self.fontMetrics()
        side = self._side_pad()
        # boundingRect 对中文比 horizontalAdvance 更稳；+2 = 左右 1px 边框
        try:
            text_w = fm.boundingRect(self.text() or "").width()
        except Exception:
            text_w = fm.horizontalAdvance(self.text() or "")
        w = max(1, int(text_w) + side * 2 + 2)
        self.setFixedSize(w, self._H)
        return w

    def sizeHint(self):
        return QSize(self.width() or self._recalc_width(), self._H)

    def minimumSizeHint(self):
        return self.sizeHint()

    def apply_chrome(self, bg: str, fg: str, bd: str, hover: str) -> None:
        """背景/字色/边框；水平 padding=0，空隙靠 fixed 宽度 + 文字居中。"""
        # 不用 CSS 水平 padding：全局 QPushButton 的 14px 与 QSS sizeHint 会叠宽。
        # 宽 = 字宽 + 左右各 1 汉字，文字由按钮默认水平居中。
        self.setStyleSheet(
            f"QPushButton{{background:{bg};color:{fg};"
            f"border:1px solid {bd};border-radius:6px;"
            f"font-size:{self._FONT_PX}px;font-weight:400;"
            f"padding:0px;margin:0px;min-width:0px;}}"
            f"QPushButton:hover{{background:{hover};}}"
            f"QPushButton:disabled{{background:{bg};color:{fg};"
            f"border:1px solid {bd};}}"
        )
        self._recalc_width()


# ═══════════════════════════════════════════════════════════════
# 插件状态文案：单行、右侧可省略，不把「速存图片」卡撑宽
# （卡片宽度只跟 row_top stretch 百分比走）
# ═══════════════════════════════════════════════════════════════
class _PluginStatusLabel(QLabel):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._full = text or ""
        self.setWordWrap(False)
        self.setMinimumWidth(0)
        # Ignored：布局算最小宽度时不看全文 sizeHint，避免长文案顶宽整卡
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

    def set_full_text(self, text: str):
        self._full = str(text or "")
        self._refresh_elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._refresh_elide()

    def _refresh_elide(self):
        w = max(0, self.width())
        if w <= 1:
            self.setText(self._full)
            return
        self.setText(self.fontMetrics().elidedText(self._full, Qt.ElideRight, w))


# ═══════════════════════════════════════════════════════════════
# 运行记录列表：空白点击取消选中；新增条目始终滚到最底
# ═══════════════════════════════════════════════════════════════
class FastRunLogList(QListWidget):
    """速存图文「运行记录」专用列表。

    · 新增条目：始终选中最新一条，并强制滚屏到该条（即使此前已有选中）
    · 点空白：取消选中
    """

    def addItem(self, *args, **kwargs):
        super().addItem(*args, **kwargs)
        # 新记录事件：跳到新记录 + 滚屏（覆盖旧选中）
        self._focus_latest(force_select=True)

    def _focus_latest(self, force_select: bool = True):
        """定位到最新一条：可选设为当前选中，并保证视口滚到该条。"""
        try:
            n = self.count()
            if n <= 0:
                return
            last = self.item(n - 1)
            if last is None:
                return
            if force_select and self.currentItem() is not last:
                # 阻塞信号会漏预览刷新；允许 currentItemChanged 更新预览
                self.setCurrentItem(last)
            self.scrollToItem(last, QAbstractItemView.PositionAtBottom)
            self.scrollToBottom()
            # 布局完成后再滚一次，避免「已选中却停在半截」
            QTimer.singleShot(0, lambda: self._scroll_latest_again())
        except Exception:
            pass

    def _scroll_latest_again(self):
        try:
            n = self.count()
            if n <= 0:
                return
            last = self.item(n - 1)
            if last is None:
                return
            self.scrollToItem(last, QAbstractItemView.PositionAtBottom)
            self.scrollToBottom()
        except Exception:
            pass

    def mousePressEvent(self, event):
        # 点在条目空白区域：取消选中（预览区随 currentItemChanged(None) 清空）
        it = self.itemAt(event.pos())
        if it is None:
            self.clearSelection()
            self.setCurrentRow(-1)
            event.accept()
            return
        super().mousePressEvent(event)


# ═══════════════════════════════════════════════════════════════
# 预览标签（格式对齐「截图工具」PageScreenshot.PreviewLabel）
# · 预览内容始终绑定「当前选中的运行记录」
# · empty：无选中 / 解析中空记录；image：已保存图片；
#   symbol：文件夹/文本等操作（大图标 + 名称）；text：其它文案记录
# · 尺寸随控件 resize 重算；minimumSizeHint 钉死，避免长文字把窗口顶高
# ═══════════════════════════════════════════════════════════════
class FastPreviewLabel(QLabel):
    _PLACEHOLDER = "（选中左侧记录可预览）"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._src = None          # QPixmap or None
        self._mode = "empty"      # empty | image | symbol | text
        self.setAlignment(Qt.AlignCenter)
        # 主窗最小约 700 内容宽时预览区约 2/5，140 为可用下限，避免顶高窗口
        self.setMinimumSize(140, 140)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._apply_style()
        self.setText(self._PLACEHOLDER)

    def minimumSizeHint(self):
        # 阻断 wordWrap 文本的 heightForWidth 向上传染（同截图预览的尺寸兜底思路）
        return QSize(140, 140)

    def sizeHint(self):
        return QSize(220, 220)

    def refresh_theme(self, *_):
        self._apply_style()

    def _apply_style(self):
        self.setStyleSheet(f"background:transparent; color:{tk('text_dim')};")

    def show_empty(self, hint: str = None):
        """空记录 / 无选中：居中占位（解析中的空记录也走这里）。"""
        self._src = None
        self._mode = "empty"
        self._apply_style()
        self.clear()
        self.setTextFormat(Qt.PlainText)
        self.setAlignment(Qt.AlignCenter)
        self.setText(hint if hint is not None else self._PLACEHOLDER)

    def set_image(self, pixmap):
        self._src = pixmap if (pixmap is not None and not pixmap.isNull()) else None
        if self._src is None:
            self.show_empty()
            return
        self._mode = "image"
        self._apply_style()
        self.clear()
        self.setTextFormat(Qt.PlainText)
        self.setAlignment(Qt.AlignCenter)
        self._rescale()

    def set_symbol_preview(self, glyph: str, title: str):
        """操作类记录预览：居中大图标 + 名称（文件夹 / 文本文件等）。"""
        self._src = None
        self._mode = "symbol"
        self._apply_style()
        self.clear()
        self.setAlignment(Qt.AlignCenter)
        self.setTextFormat(Qt.RichText)
        g = (glyph or "•").strip() or "•"
        name = (title or "").strip() or "（未命名）"
        # 简易转义，避免标题里的 <>& 破坏 HTML
        for a, b in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;")):
            name = name.replace(a, b)
        color = tk("text")
        self.setText(
            f'<div style="text-align:center;">'
            f'<div style="font-size:52px;line-height:1;">{g}</div>'
            f'<div style="margin-top:0;font-size:14px;font-weight:600;'
            f'color:{color};">{name}</div>'
            f"</div>"
        )

    def set_text_content(self, text: str):
        """非图片运行记录：主区域展示文字（顶左对齐，格式贴近截图预览的主区占用）。"""
        self._src = None
        self._mode = "text"
        self._apply_style()
        self.clear()
        self.setTextFormat(Qt.PlainText)
        self.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.setText(text or self._PLACEHOLDER)

    def _rescale(self):
        if self._mode != "image" or self._src is None:
            return
        self.setPixmap(self._src.scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, e):
        self._rescale()
        super().resizeEvent(e)


# ═══════════════════════════════════════════════════════════════
# PageFastSave
# ═══════════════════════════════════════════════════════════════
class PageFastSave(QWidget):
    STATE_IDLE    = 0
    STATE_IMGSAVE = 1

    log_sig     = pyqtSignal(str)
    # 插件状态更新信号（color, text, bold）——供后台检测线程安全地切回主线程更新 UI
    plugin_status_sig = pyqtSignal(str, str, str, bool)
    # 速存操作（存图/存文本/建文件夹）成功时发射，请求主窗口切到「速存图文」页
    request_show = pyqtSignal()
    # WS SAVE 解析：开始解析（url）→ 主线程添加「解析中」闪烁记录
    parse_start_sig = pyqtSignal(str)
    # WS SAVE 解析完成（url, filepath, success）→ 主线程更新记录为成功/失败
    # filepath 为本地完整路径（失败时可能为空字符串）
    parse_done_sig = pyqtSignal(str, str, bool)
    # F6 记录模式切换 → 通知主窗口关闭/恢复自动下载
    record_mode_toggled_sig = pyqtSignal(str)

    @staticmethod
    def _typo(w, name):
        w.setProperty("typo", name)
        w.style().unpolish(w)
        w.style().polish(w)

    @staticmethod
    def _set_qprop(w, name, value):
        """设置一个 Qt 动态属性并刷新样式——用于命中 app.qss / app_light.qss 里的属性选择器。"""
        w.setProperty(name, value)
        w.style().unpolish(w)
        w.style().polish(w)

    @staticmethod
    def _make_glyph_icon(glyph: str, px: int = 16, color: str = "#8fa3d9") -> QIcon:
        """兼容旧调用；实现已迁到 style_all.make_glyph_icon。"""
        return make_glyph_icon(glyph, px=px, color=color)

    # ===== 窗口高度 BUG 根治（与 Grok 诊断一致：heightForWidth 把虚高传给主窗口）=====
    # 本页含 wordWrap 自动换行标签，会让整页 hasHeightForWidth()=True。Qt 据此按当前
    # （较窄的）宽度把页面高度算大，这个虚高顺着布局链一路传到主窗口，抬高了窗口的
    # “首选高度”，于是真机上一拖标题栏，窗口就往首选高度长高、且缩不回去（切到没有此
    # 特性的页面才松开）。对照 6 个正常页面：它们 hasHeightForWidth()=False，窗口首选
    # 高度稳定在侧边栏决定的 836（<900），从不长高。
    # 修法：本页对外声明“高度不随宽度变化”，并把对外“首选高度”压到不超过侧边栏——
    #   · 标签内部仍照常换行，不截断文字（只是不把该属性外传给窗口）；
    #   · 实际布局仍会把可用高度分配给本页，Expanding 子控件照常填满，视觉无变化。
    def hasHeightForWidth(self):
        return False

    def sizeHint(self):
        s = super().sizeHint()
        return QSize(s.width(), min(s.height(), 700))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        h = self.height()
        if h > 0:
            top_h = max(170, int(h * 0.35))
            for _card in (self._card_img, self._card_txt, self._card_mkdir, self._card_check):
                _card.setFixedHeight(top_h)
        # 底部：运行记录 与 预览区 各占 50% 宽，写死为固定值，
        # 不受两侧内容 sizeHint / 长文字撑宽影响
        w = self.width()
        if w > 0:
            run = getattr(self, "_card_runlog", None)
            prev = getattr(self, "_card_preview", None)
            if run is not None and prev is not None:
                gap = max(1, int(getattr(self, "_bottom_gap", 6)))
                half = max(1, (w - gap) // 2)
                run.setFixedWidth(half)
                prev.setFixedWidth(max(1, w - gap - half))
        # 预览区变宽/变窄时重算文件名省略，分辨率行始终保留
        try:
            cur = self.list_widget.currentItem() if hasattr(self, "list_widget") else None
            if cur is not None and self._runlog_kind(cur) == "image":
                path = self._resolve_runlog_image_path(cur)
                if path and hasattr(self, "preview") and getattr(self.preview, "_src", None) is not None:
                    self._set_image_preview_meta(path, self.preview._src)
        except Exception:
            pass

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setSpacing(6)
        # 与系统总览一致：ContentRoot 已有左右内边距，页面不再叠第二层
        lay.setContentsMargins(0, 0, 0, 0)

        # ══════════ 顶部三卡片：速存图片 / 速存文本 / 速建文件夹 ══════════
        row_top = QHBoxLayout()
        row_top.setSpacing(6)
        lay.addLayout(row_top)

        self._theme_frames = []
        self._theme_titles = []
        self._theme_secondary_bold = []
        self._theme_secondary_plain = []

        # ── 速存图片 ──
        gb_img  = make_card("CardFastAuto")
        self._theme_frames.append(gb_img)
        img_box = QVBoxLayout(gb_img)
        img_box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        img_box.setSpacing(0)
        title_img = install_card_title(gb_img, img_box, "速存图片")
        self._theme_titles.append(title_img)

        self.chk_imgonly = QCheckBox("启用速存图片")
        self.chk_imgonly.setVisible(False)  # 子开关不展示，由总开关控制

        # 一排：开关 + 启动速存
        self.chk_enable_all = QCheckBox("启动速存")
        self.chk_enable_all.setStyleSheet("background: transparent;")
        self.chk_enable_all.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        img_box.addWidget(self.chk_enable_all, 0, Qt.AlignLeft)

        # 二排：状态按钮（三色胶囊：绿=已连接 / 黄=等待中 / 红=不可用）
        self.btn_status = QPushButton("检测中")
        self.btn_status.setCursor(Qt.PointingHandCursor)
        self.btn_status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._apply_status_btn_style("#f0a500", "检测中", bold=False)
        self.btn_status.clicked.connect(self._diagnose)
        img_box.addWidget(self.btn_status)

        # 三排：Xs前心跳
        self.lbl_heartbeat = QLabel("——")
        self.lbl_heartbeat.setWordWrap(False)
        self.lbl_heartbeat.setStyleSheet("background: transparent;")
        self.lbl_heartbeat.setAlignment(Qt.AlignCenter)
        img_box.addWidget(self.lbl_heartbeat)

        # 四排：操作说明
        self.lbl_img_hint = QLabel("鼠标停留在浏览器的图片上后，按设置的快捷键进行存图（Alt+1）")
        self.lbl_img_hint.setWordWrap(True)
        self.lbl_img_hint.setStyleSheet("background: transparent;")
        self.lbl_img_hint.setAlignment(Qt.AlignLeft)
        self._typo(self.lbl_img_hint, "muted")
        img_box.addWidget(self.lbl_img_hint)

        # 快捷键下拉框保留对象（内部逻辑仍会用到），但不再显示在界面上
        self.combo_img_hotkey = QComboBox()
        self.combo_img_hotkey.setObjectName("FastSaveHotkey")
        self.combo_img_hotkey.addItems(["Alt+1"])
        self.combo_img_hotkey.setItemIcon(0, self._make_glyph_icon("⌨", color=content_secondary_color()))
        self.combo_img_hotkey.setCurrentText("Alt+1")
        self.combo_img_hotkey.setEnabled(False)   # 固定 Alt+1，不提供改键
        self.combo_img_hotkey.setVisible(False)

        # 保存路径 —— 全局「文件夹路径」样式（圆角框 + 📁）
        row_path = QHBoxLayout()
        row_path.setSpacing(4)
        self.save_path = QLineEdit("")
        self._path_icon_action = apply_folder_path_edit(self.save_path)
        row_path.addWidget(self.save_path, 1)
        img_box.addLayout(row_path)
        img_box.addSpacing(6)

        # 全局「中按钮」：高 29px，与上方文件夹路径框齐高
        self.btn_choose_dir = apply_medium_button(QPushButton("另选目录"))
        img_box.addWidget(self.btn_choose_dir)
        img_box.addStretch(1)

        # ── 速存文本 ──
        gb_txt  = make_card("CardFastManual")
        self._theme_frames.append(gb_txt)
        txt_box = QVBoxLayout(gb_txt)
        txt_box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        txt_box.setSpacing(0)
        title_txt = install_card_title(gb_txt, txt_box, "速存文本")
        self._theme_titles.append(title_txt)

        self.chk_manual = QCheckBox("启用速存文本")
        self.chk_manual.setVisible(False)

        # ── 原速存文本 ──
        lbl_hint = QLabel("可快速创建主体同名文本文件")
        lbl_hint.setWordWrap(True)
        lbl_hint.setStyleSheet("background: transparent;")
        self._typo(lbl_hint, "muted")
        txt_box.addWidget(lbl_hint)
        txt_box.addSpacing(6)
        self._txt_hotkey_group = QButtonGroup(self)
        row_radios = QHBoxLayout()
        row_radios.setSpacing(4)
        self._txt_radio = {}
        for key in ["F3", "F4"]:
            rb = QRadioButton(key)
            rb.setStyleSheet("background: transparent;")
            self._txt_radio[key] = rb
            self._txt_hotkey_group.addButton(rb)
            row_radios.addWidget(rb)
        self._txt_radio["F4"].setChecked(True)
        row_radios.addStretch(1)
        txt_box.addLayout(row_radios)
        txt_box.addSpacing(6)

        # ── 记录模式 ──
        record_row = QHBoxLayout()
        record_row.setSpacing(8)
        self.btn_record = QPushButton("开始记录F6")
        self.btn_record.setCursor(Qt.PointingHandCursor)
        self.btn_record.clicked.connect(self._btn_record_clicked)
        # 批处理中冻结：开始记录按钮 + 记录卡（与侧栏自动下载开关一致）
        self._record_btn_frozen = False
        self._record_cards_frozen = False
        self._restyle_record_btn()
        record_row.addWidget(self.btn_record)
        self.chk_new_record = QCheckBox("随机记录名")
        self.chk_new_record.setStyleSheet("background: transparent;")
        record_row.addWidget(self.chk_new_record)
        record_row.addStretch(1)
        txt_box.addLayout(record_row)
        txt_box.addSpacing(2)

        self.lbl_record_hint = QLabel("点击「开始记录」后复制即存入文件")
        self.lbl_record_hint.setWordWrap(True)
        self.lbl_record_hint.setStyleSheet("background:transparent;padding:2px 0;")
        self.lbl_record_hint.setAlignment(Qt.AlignLeft)
        self._typo(self.lbl_record_hint, "muted")
        txt_box.addWidget(self.lbl_record_hint)
        txt_box.addSpacing(2)

        # 记录卡片网格（可滚动）
        self._record_scroll = QScrollArea()
        self._record_scroll.setFrameShape(QFrame.NoFrame)
        self._record_scroll.setWidgetResizable(True)
        self._record_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._record_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._record_scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
        )
        self._record_grid_widget = QWidget()
        self._record_grid_widget.setStyleSheet("background:transparent;")
        self._record_grid_layout = FlowLayout(self._record_grid_widget, margin=0, h_spacing=4, v_spacing=4)
        self._record_scroll.setWidget(self._record_grid_widget)
        txt_box.addWidget(self._record_scroll, 1)

        # ── 速建文件夹（回到顶部，与 A/B/C 同排） ──
        gb_mkdir = make_card("CardFastMkdir")
        gb_mkdir.setMinimumWidth(160)
        self._theme_frames.append(gb_mkdir)
        mkdir_box = QVBoxLayout(gb_mkdir)
        mkdir_box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        mkdir_box.setSpacing(0)
        title_mkdir = install_card_title(gb_mkdir, mkdir_box, "速建文件夹")
        self._theme_titles.append(title_mkdir)

        self.chk_mkdir = QCheckBox("启用速建文件夹")
        self.chk_mkdir.setVisible(False)  # 子开关不展示，由速存启用总开关控制

        row_mkdir_key = QHBoxLayout()
        row_mkdir_key.setSpacing(4)
        lbl_seq = QLabel("顺序新建")
        lbl_seq.setStyleSheet(f"background:transparent; color:{content_secondary_color()}; font-weight:600; padding:0;")
        self._theme_secondary_bold.append(lbl_seq)
        row_mkdir_key.addWidget(lbl_seq)
        self._mkdir_hotkey_group = QButtonGroup(self)
        self._mkdir_radio = {}
        for key in ["F7", "F8"]:
            rb = QRadioButton(key)
            rb.setStyleSheet("background: transparent;")
            self._mkdir_radio[key] = rb
            self._mkdir_hotkey_group.addButton(rb)
            row_mkdir_key.addWidget(rb)
        self._mkdir_radio["F8"].setChecked(True)
        row_mkdir_key.addStretch(1)
        mkdir_box.addLayout(row_mkdir_key)
        mkdir_box.addSpacing(4)

        row_mkdir_f9 = QHBoxLayout()
        row_mkdir_f9.setSpacing(4)
        lbl_f9 = QLabel("F9 直接新建")
        lbl_f9.setStyleSheet(f"background:transparent; color:{content_secondary_color()}; padding:0;")
        self._theme_secondary_plain.append(lbl_f9)
        row_mkdir_f9.addWidget(lbl_f9)
        self.mkdir_name = QLineEdit("Grok")
        self.mkdir_name.setPlaceholderText("默认 Grok")
        self.mkdir_name.setFixedWidth(80)
        self.mkdir_name.setProperty("inputStyle", "underline")
        self.mkdir_name.style().unpolish(self.mkdir_name)
        self.mkdir_name.style().polish(self.mkdir_name)
        row_mkdir_f9.addWidget(self.mkdir_name)
        row_mkdir_f9.addStretch(1)
        mkdir_box.addLayout(row_mkdir_f9)
        mkdir_box.addSpacing(2)

        row_mkdir_f10 = QHBoxLayout()
        row_mkdir_f10.setSpacing(4)
        lbl_f10 = QLabel("F10 直接新建")
        lbl_f10.setStyleSheet(f"background:transparent; color:{content_secondary_color()}; padding:0;")
        self._theme_secondary_plain.append(lbl_f10)
        row_mkdir_f10.addWidget(lbl_f10)
        self.mkdir_name_b = QLineEdit("Qwen")
        self.mkdir_name_b.setPlaceholderText("默认 Qwen")
        self.mkdir_name_b.setFixedWidth(80)
        self.mkdir_name_b.setProperty("inputStyle", "underline")
        self.mkdir_name_b.style().unpolish(self.mkdir_name_b)
        self.mkdir_name_b.style().polish(self.mkdir_name_b)
        row_mkdir_f10.addWidget(self.mkdir_name_b)
        row_mkdir_f10.addStretch(1)
        mkdir_box.addLayout(row_mkdir_f10)
        mkdir_box.addSpacing(6)

        lbl_mkdir_hint = QLabel("选中文件夹按所选键顺序新建；未选中时 F9/F10 直接新建对应文件夹")
        lbl_mkdir_hint.setWordWrap(True)
        lbl_mkdir_hint.setStyleSheet("background: transparent;")
        self._typo(lbl_mkdir_hint, "muted")
        mkdir_box.addWidget(lbl_mkdir_hint)
        mkdir_box.addStretch(1)

        # ── 序列文件检查 ──
        gb_check = make_card("CardFastCheck")
        gb_check.setMinimumWidth(110)
        self._theme_frames.append(gb_check)
        check_box = QVBoxLayout(gb_check)
        check_box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        check_box.setSpacing(0)
        title_check = install_card_title(gb_check, check_box, "序列文件检查")
        self._theme_titles.append(title_check)

        self.check_path = QLineEdit()
        self.check_path.setVisible(False)

        row_check_opts = QHBoxLayout()
        row_check_opts.setContentsMargins(0, 0, 0, 0)
        row_check_opts.setSpacing(8)
        self.chk_ignore_ext = QCheckBox(" 忽略扩展名")
        self.chk_ignore_ext.setStyleSheet("background:transparent; font-size:11px; spacing:0;")
        self.chk_ignore_ext.setChecked(True)
        self.chk_ignore_ext.toggled.connect(lambda: self._check_scan() if self._check_folder else None)
        row_check_opts.addWidget(self.chk_ignore_ext)
        # 「补全」：指定文件夹后，用序列真实缺失 + _缺失文件清单.txt 源链接 → 图集下载只补缺
        self.chk_check_fill = QCheckBox(" 补全")
        self.chk_check_fill.setStyleSheet("background:transparent; font-size:11px; spacing:0;")
        self.chk_check_fill.setChecked(False)
        self.chk_check_fill.setToolTip(
            "仅 e-hentai.org：指定文件夹时统计缺失，读取 _缺失文件清单.txt 源链接，\n"
            "转入图集「e-hentai.org」页只补序列检查得到的真实缺失（不读清单内缺失列表）。"
        )
        row_check_opts.addWidget(self.chk_check_fill)
        row_check_opts.addStretch(1)
        check_box.addLayout(row_check_opts)
        check_box.addSpacing(6)

        btn_check_browse = apply_medium_button(QPushButton("指定文件夹"))
        btn_check_browse.setCursor(Qt.PointingHandCursor)
        btn_check_browse.clicked.connect(self._check_browse_folder)
        check_box.addWidget(btn_check_browse)

        self.lbl_check_result = QLabel("")
        self.lbl_check_result.setWordWrap(True)
        self.lbl_check_result.setStyleSheet("background:transparent; font-size:13px; color:#94a3b8; padding-top:4px;")
        check_box.addWidget(self.lbl_check_result)
        check_box.addStretch(1)

        # 四卡宽度：图片 / 文本 / 速建 / 序列文件检查 = 8% / 30% / 19% / 15%
        # 四卡等高由 resizeEvent 控制：最小 170px，高度占比 35%
        self._card_img = gb_img
        self._card_txt = gb_txt
        self._card_mkdir = gb_mkdir
        self._card_check = gb_check
        for _card in (gb_img, gb_txt, gb_mkdir, gb_check):
            _card.setMinimumWidth(0)
            _card.setMinimumHeight(170)
            _card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row_top.addWidget(gb_img, 8)
        row_top.addWidget(gb_txt, 30)
        row_top.addWidget(gb_mkdir, 19)
        row_top.addWidget(gb_check, 15)

        # ══════════ 底部：运行记录（大，占主要空间） + 预览区（右侧，选中左侧记录可预览） ══════════
        row_bottom = QHBoxLayout()
        row_bottom.setSpacing(6)
        self._bottom_gap = 6
        lay.addLayout(row_bottom, 1)   # 底部区域吸收窗口剩余高度

        card_runlog = make_card("CardFastRunLog")
        self._theme_frames.append(card_runlog)
        self._card_runlog = card_runlog
        runlog_box = QVBoxLayout(card_runlog)
        runlog_box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        runlog_box.setSpacing(0)
        title_runlog = install_card_title(card_runlog, runlog_box, "运行记录")
        self._theme_titles.append(title_runlog)

        self.list_widget = FastRunLogList()
        self.list_widget.setObjectName("FastSaveList")
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.setSpacing(1)
        # 全局「简易记录」+ FastSaveList 选中：高亮底 + 左侧 4px 橙色竖条
        apply_simple_record(self.list_widget)
        try:
            self.list_widget.setFocusPolicy(Qt.StrongFocus)
            # 允许点空白取消选中（见 FastRunLogList.mousePressEvent）
            self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        except Exception:
            pass
        runlog_box.addWidget(self.list_widget, 1)

        # 宽度：运行记录 与 预览区 各占 50%
        row_bottom.addWidget(card_runlog, 1)

        # ── 预览区：格式对齐「截图工具 → 截图预览」──────────────────────────
        #   主区 stretch 吃满高度（占位/图/文字）+ 底部固定 meta 行
        card_preview = make_card("CardFastPreview")
        self._theme_frames.append(card_preview)
        self._card_preview = card_preview
        preview_box = QVBoxLayout(card_preview)
        preview_box.setContentsMargins(CARD_LEFT_GAP, CARD_TOP_GAP, CARD_RIGHT_GAP, CARD_BOTTOM_GAP)
        preview_box.setSpacing(0)
        title_preview = install_card_title(card_preview, preview_box, "预览区")
        self._theme_titles.append(title_preview)

        self.preview = FastPreviewLabel()
        preview_box.addWidget(self.preview, 1)

        self.lbl_preview_meta = QLabel()
        self.lbl_preview_meta.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        # 不换行：长文件名只占第 1 行（超出省略），第 2 行固定留给「宽×高 · 体积」，
        # 避免 wordWrap 把文件名拆成两行后把分辨率挤出 40px 可视区。
        self.lbl_preview_meta.setWordWrap(False)
        self.lbl_preview_meta.setFixedHeight(40)
        self.lbl_preview_meta.setProperty("typo", "muted")
        self.lbl_preview_meta.setVisible(False)
        self.lbl_preview_meta.setTextInteractionFlags(Qt.TextSelectableByMouse)
        preview_box.addWidget(self.lbl_preview_meta)

        row_bottom.addWidget(card_preview, 1)

        self.list_widget.currentItemChanged.connect(self._on_runlog_item_selected)

        self._theme_glyph_actions = [
            (self._path_icon_action, "📁"),
        ]
        self._theme_glyph_combo_items = [
            (self.combo_img_hotkey, 0, "⌨"),
        ]

        # ══════════ 信号与槽 ══════════
        self.log_sig.connect(self._append_log)
        self.plugin_status_sig.connect(self._apply_plugin_ui)
        self.btn_choose_dir.clicked.connect(self._choose_dir)
        self.chk_imgonly.toggled.connect(self._on_imgonly_toggled)
        self.chk_manual.toggled.connect(self._on_manual_toggled)
        self.combo_img_hotkey.currentTextChanged.connect(self._on_img_hotkey_changed)
        self._txt_hotkey_group.buttonClicked.connect(self._on_txt_hotkey_changed)
        self._mkdir_hotkey_group.buttonClicked.connect(self._on_mkdir_hotkey_changed)
        self.save_path.textChanged.connect(self._on_path_edited)
        # 速存启用总开关
        self.chk_enable_all.toggled.connect(self._on_enable_all_toggled)
        # 子功能变化时同步总开关显示
        self.chk_imgonly.toggled.connect(self._sync_enable_all_display)
        self.chk_manual.toggled.connect(self._sync_enable_all_display)
        self.chk_mkdir.toggled.connect(self._sync_enable_all_display)

        # ══════════ 状态 ══════════
        # 目录监控：把浏览器暂存目录里的新图片移动到保存路径
        self._archived_seen = set()   # 当“保存路径==暂存区”时，用于记录已见过的文件
        self._imgonly_timer = QTimer(self)
        self._imgonly_timer.setInterval(1000)
        self._imgonly_timer.timeout.connect(self._scan_imgonly_files)

        # 心跳：每 5 秒自动刷新插件连接状态
        self._closing = False   # 主窗口关闭时置 True，阻止后台检测线程再碰已销毁的界面
        self._nm_check_timer = QTimer(self)
        self._nm_check_timer.setInterval(5000)
        self._nm_check_timer.timeout.connect(self._update_nm_status)
        self._nm_check_timer.start()
        QTimer.singleShot(1000, self._update_nm_status)  # 启动 1s 后做第一次检测

        # 速存文本热键
        self._hotkey_id              = 0x1002
        self._hotkey_filter          = None
        self._hotkey_registered_key  = None

        self._sc_manual = QShortcut(QKeySequence(self._current_txt_key()), self)
        self._sc_manual.activated.connect(self.manual_fast_save)
        self._sc_manual.setEnabled(False)

        # 速建文件夹热键（顺序新建，F7-F8 可选）
        self._mkdir_hotkey_id     = 0x1003
        self._mkdir_hotkey_filter = None
        self._mkdir_hotkey_registered = False
        # 直接新建 A（F9）
        self._mkdir_direct_id     = 0x1004
        self._mkdir_direct_filter = None
        # 直接新建 B（F10）
        self._mkdir_direct_b_id     = 0x1005
        self._mkdir_direct_b_filter = None
        self._mkdir_direct_registered = False
        self._mkdir_direct_b_registered = False
        self.chk_mkdir.toggled.connect(self._on_mkdir_toggled)

        # ── F6 记录模式热键（独立于按钮，全局生效）──
        self._recording_hotkey_id = 0x1006
        self._recording_hotkey_filter = None
        self._recording_hotkey_registered = False
        self._register_recording_hotkey()

        self.clipboard = QApplication.clipboard()
        self.state     = self.STATE_IDLE

        # ── F6 记录模式 ──
        self._recording = False
        self._record_file = ""
        self._record_count = 0
        self._record_base_count = 0  # 已有记录行数（继续记录时从文件读入）
        self._record_clip_ignore = False
        # 防重复：本会话内已写入 + 启动时从全文件载入的规范化行集合
        self._record_seen = set()
        self.clipboard.dataChanged.connect(self._on_record_clipboard)

        self._init_save_dir()
        self._enter_idle(reset_switches=True)
        # 默认不勾「启用速存」；启动时由 records/user.txt 恢复用户习惯
        self._sync_enable_all_display()
        # 偏好恢复是否已写过「速存已按习惯启用」摘要（防重复 apply 刷屏）
        self._prefs_restored_once = False
        # quiet 路径下「同类状态/失败」只记一次的 key 集合
        self._runlog_once_keys = set()

        # ── WS SAVE 回调注册（必须在 start_ws_server 之前，避免插件连上瞬间丢消息）──
        self._parsing_items = {}   # url → QListWidgetItem（正在解析中的条目）
        self._saved_filenames = set()  # 去重：已记录为「已保存图片」的文件名（小写）

        # 注册模块级回调：收到 SAVE → 信号到主线程加「解析中」记录
        import pages.page_fast_save as _pfs
        _pfs._ws_save_started_cb = lambda url: self.parse_start_sig.emit(url)
        _pfs._ws_save_done_cb    = lambda url, name, ok: self.parse_done_sig.emit(url, name, ok)

        self.parse_start_sig.connect(self._on_parse_start)
        self.parse_done_sig.connect(self._on_parse_done)

        # 插件 WebSocket：进入页面生命周期后再监听，import 时不起线程
        start_ws_server()

        # 启动时加载今天的记录文件按钮
        self._init_record_file()

        theme.changed.connect(self._apply_theme)

        # 保存目录由「关于」弹窗统一管理，页面 UI 隐藏
        self.save_path.setVisible(False)
        self.btn_choose_dir.setVisible(False)

    # ────────────────────────────────────────
    # 用户习惯（records/user.txt · fast_save 段）
    # ────────────────────────────────────────
    def export_settings(self) -> dict:
        """导出当前速存相关设置，供 user_prefs 落盘。"""
        return {
            "enabled": bool(self.is_any_active()),
            "save_path": (self.save_path.text() or "").strip(),
            "txt_hotkey": self._current_txt_key(),
            "mkdir_hotkey": self._current_mkdir_key(),
            "mkdir_name_f9": (self.mkdir_name.text() or "").strip() or "Grok",
            "mkdir_name_f10": (self.mkdir_name_b.text() or "").strip() or "Qwen",
            "record_random_name": bool(self.chk_new_record.isChecked()),
            "check_ignore_ext": bool(self.chk_ignore_ext.isChecked()),
            "check_fill": bool(
                getattr(self, "chk_check_fill", None) is not None
                and self.chk_check_fill.isChecked()
            ),
        }

    def apply_settings(self, d: dict):
        """从 user.txt 恢复设置。未知/空字段保持现状。"""
        if not isinstance(d, dict):
            return
        path = (d.get("save_path") or "").strip()
        if path:
            self.save_path.blockSignals(True)
            try:
                self.save_path.setText(path)
            finally:
                self.save_path.blockSignals(False)
            _ws_set_save_dir(path)
            self._require_writable_path(interactive=False)

        txt_key = str(d.get("txt_hotkey") or "").upper()
        if txt_key in getattr(self, "_txt_radio", {}):
            self._txt_radio[txt_key].setChecked(True)
            if hasattr(self, "_sc_manual"):
                self._sc_manual.setKey(QKeySequence(txt_key))

        mkdir_key = str(d.get("mkdir_hotkey") or "").upper()
        if mkdir_key in getattr(self, "_mkdir_radio", {}):
            self._mkdir_radio[mkdir_key].setChecked(True)

        name_f9 = (d.get("mkdir_name_f9") or "").strip()
        if name_f9:
            self.mkdir_name.setText(name_f9)
        name_f10 = (d.get("mkdir_name_f10") or "").strip()
        if name_f10:
            self.mkdir_name_b.setText(name_f10)

        record_random = d.get("record_random_name")
        if record_random is not None and hasattr(self, "chk_new_record"):
            self.chk_new_record.setChecked(bool(record_random))

        check_ext = d.get("check_ignore_ext")
        if check_ext is not None and hasattr(self, "chk_ignore_ext"):
            self.chk_ignore_ext.setChecked(bool(check_ext))

        check_fill = d.get("check_fill")
        if check_fill is not None and hasattr(self, "chk_check_fill"):
            self.chk_check_fill.setChecked(bool(check_fill))

        # 总开关最后应用（联动子功能与热键；quiet 避免运行记录被状态行刷屏）
        # 注：总览保存 / 反复 apply 时若再写「已启用」，同一会话会出现成套重复条目
        enabled = bool(d.get("enabled", False))
        self.set_all_features(enabled, quiet=True)
        self._sync_enable_all_display()
        # 仅首次从偏好恢复时写一行摘要；之后再 apply 不再追加
        if not getattr(self, "_prefs_restored_once", False):
            self._prefs_restored_once = True
            if enabled:
                p = (self.save_path.text() or "").strip() or path
                tip = "🟢 速存已按习惯启用"
                if p:
                    tip += f" → {p}"
                try:
                    self.list_widget.addItem(tip)
                except Exception:
                    pass

    def _apply_theme(self, *_args):
        """主题切换时重刷卡片外观（内联样式方案，不吃全局 QSS 级联，需手动重刷）。
        注：记录列表虚线框 / 下划线输入框 / 迷你按钮已迁移到 app.qss、app_light.qss 的
        属性选择器（recordStyle / inputStyle / kind），会随应用级样式表切换自动生效，
        这里只需要处理内联画色的部分：卡片外观、标题、次要文字、以及程序绘制的符号图标。"""
        for frame in self._theme_frames:
            restyle_card_frame(frame)
        for lbl in self._theme_titles:
            restyle_card_title(lbl)
        secondary = content_secondary_color()
        for lbl in self._theme_secondary_bold:
            lbl.setStyleSheet(f"background:transparent; color:{secondary}; font-weight:600;")
        for lbl in self._theme_secondary_plain:
            lbl.setStyleSheet(f"background:transparent; color:{secondary};")
        if hasattr(self, "save_path"):
            restyle_folder_path_edit(self.save_path, getattr(self, "_path_icon_action", None))
        for action, glyph in getattr(self, "_theme_glyph_actions", []):
            if action is getattr(self, "_path_icon_action", None):
                continue  # 已由 restyle_folder_path_edit 处理
            action.setIcon(self._make_glyph_icon(glyph, color=secondary))
        for combo, idx, glyph in getattr(self, "_theme_glyph_combo_items", []):
            combo.setItemIcon(idx, self._make_glyph_icon(glyph, color=secondary))
        if hasattr(self, "preview"):
            self.preview.refresh_theme()
        if hasattr(self, "btn_record"):
            if getattr(self, "_record_btn_frozen", False):
                self._restyle_record_btn("frozen")
            else:
                self._restyle_record_btn("recording" if self._recording else "idle")
        if hasattr(self, "_record_grid_layout"):
            self._refresh_record_cards()

    _IMG_LOG_RE = re.compile(
        r"已保存图片[：:]\s*(.+?\.(?:jpg|jpeg|png|gif|bmp|webp|avif|tiff?|ico))",
        re.IGNORECASE,
    )
    _FOLDER_LOG_RE = re.compile(
        r"(?:速建文件夹成功|直接新建成功)[：:]\s*(.+)$"
    )
    _TEXT_LOG_RE = re.compile(
        r"速存文本成功[：:]\s*(.+)$"
    )

    @staticmethod
    def _is_remote_url(s: str) -> bool:
        s = (s or "").strip().lower()
        return s.startswith(("http://", "https://", "blob:", "data:"))

    def _runlog_kind(self, item) -> str:
        """当前记录类型：parsing | image | folder | textfile | fail | generic。"""
        if item is None:
            return "generic"
        text = item.text() or ""
        if "解析中" in text:
            return "parsing"
        stored = item.data(Qt.UserRole)
        if isinstance(stored, str) and self._is_remote_url(stored):
            return "parsing"
        if "已保存图片" in text:
            return "image"
        if "速建文件夹成功" in text or "直接新建成功" in text:
            return "folder"
        if "速存文本成功" in text:
            return "textfile"
        if "解析失败" in text:
            return "fail"
        return "generic"

    def _clear_preview(self, hint: str = None):
        """预览区回到占位（无图、无 meta）。"""
        try:
            self.preview.show_empty(hint)
            self.lbl_preview_meta.clear()
            self.lbl_preview_meta.setToolTip("")
            self.lbl_preview_meta.setVisible(False)
        except Exception:
            pass

    @staticmethod
    def _fmt_file_size(n: int) -> str:
        try:
            n = int(n)
        except (TypeError, ValueError):
            return ""
        if n < 0:
            return ""
        if n < 1024:
            return f"{n} B"
        if n < 1024 * 1024:
            return f"{n / 1024:.0f} KB"
        if n < 1024 * 1024 * 1024:
            return f"{n / (1024 * 1024):.2f} MB"
        return f"{n / (1024 * 1024 * 1024):.2f} GB"

    def _set_image_preview_meta(self, path: str, pix) -> None:
        """图片预览下方信息：文件名 + 分辨率（必显）+ 体积；完整内容进 tooltip。"""
        name = os.path.basename(path) if path else ""
        w = int(pix.width()) if pix is not None and not pix.isNull() else 0
        h = int(pix.height()) if pix is not None and not pix.isNull() else 0
        size_s = ""
        try:
            if path and os.path.isfile(path):
                size_s = self._fmt_file_size(os.path.getsize(path))
        except Exception:
            size_s = ""

        # 第 2 行：分辨率固定在前，避免被长文件名挤掉（这是用户反馈的「少了分辨率」根因）
        res = f"{w}×{h}" if (w > 0 and h > 0) else "分辨率 —"
        line2 = f"{res}  ·  {size_s}" if size_s else res

        # 第 1 行：按标签宽度中间省略，保证始终只有两行落在 40px 内
        disp_name = name or "（未命名）"
        try:
            fm = self.lbl_preview_meta.fontMetrics()
            avail = max(40, self.lbl_preview_meta.width() - 8)
            if avail > 40:
                disp_name = fm.elidedText(disp_name, Qt.ElideMiddle, avail)
        except Exception:
            pass

        meta_text = f"{disp_name}\n{line2}"
        self.lbl_preview_meta.setText(meta_text)
        tip_lines = []
        if path:
            tip_lines.append(path)
        tip_lines.append(line2)
        self.lbl_preview_meta.setToolTip("\n".join(tip_lines))
        self.lbl_preview_meta.setVisible(True)

    def _runlog_stored_path(self, item) -> str:
        """UserRole 里存的本地路径（文件或文件夹）；远程 URL 不算。"""
        if item is None:
            return ""
        stored = item.data(Qt.UserRole)
        if not isinstance(stored, str):
            return ""
        stored = stored.strip()
        if not stored or self._is_remote_url(stored):
            return ""
        return stored

    def _resolve_runlog_image_path(self, item) -> str:
        """从运行记录条目解析本地图片路径。
        优先 Qt.UserRole；否则用「已保存图片」文案文件名 + 保存目录回退。
        """
        if item is None or self._runlog_kind(item) != "image":
            return ""
        stored = self._runlog_stored_path(item)
        if stored and os.path.isfile(stored):
            return stored

        text = item.text() or ""
        m = self._IMG_LOG_RE.search(text)
        if not m:
            return ""
        filename = m.group(1).strip().replace("/", os.sep).replace("\\", os.sep)
        save_dir = (self.save_path.text() or "").strip()
        candidates = []
        if save_dir:
            candidates.append(os.path.join(save_dir, filename))
            base = os.path.basename(filename)
            if base and base != filename:
                candidates.append(os.path.join(save_dir, base))
        for c in candidates:
            if c and os.path.isfile(c):
                return c
        return ""

    def _resolve_runlog_folder(self, item) -> tuple:
        """返回 (folder_path_or_empty, display_name)。"""
        text = item.text() or "" if item is not None else ""
        stored = self._runlog_stored_path(item)
        if stored and os.path.isdir(stored):
            return stored, os.path.basename(stored.rstrip("\\/")) or stored
        m = self._FOLDER_LOG_RE.search(text)
        name = (m.group(1).strip() if m else "").strip()
        # 文案里可能是完整路径
        if name and (os.path.isdir(name) or "\\" in name or "/" in name):
            leaf = os.path.basename(name.rstrip("\\/")) or name
            return (name if os.path.isdir(name) else ""), leaf
        return "", name

    def _resolve_runlog_textfile(self, item) -> tuple:
        """返回 (file_path_or_empty, display_name)。"""
        text = item.text() or "" if item is not None else ""
        stored = self._runlog_stored_path(item)
        if stored and os.path.isfile(stored):
            return stored, os.path.basename(stored)
        m = self._TEXT_LOG_RE.search(text)
        name = (m.group(1).strip() if m else "").strip()
        return "", name

    def _on_runlog_item_selected(self, current, _previous):
        """预览区始终绑定当前选中记录：
        · 无选中 → 默认占位
        · 解析中（空记录）→ 空预览
        · 已保存图片 → 图片 + 文件名/尺寸
        · 新建文件夹 → 文件夹图标 + 文件夹名
        · 速存文本 → 文本图标 + 文件名
        · 其它 → 记录原文
        """
        if current is None:
            self._clear_preview()
            return

        kind = self._runlog_kind(current)
        text = current.text() or ""

        # 解析中 = 这条记录本身还是空的，预览就是空
        if kind == "parsing":
            self._clear_preview("（解析中…）")
            return

        if kind == "image":
            full_path = self._resolve_runlog_image_path(current)
            if full_path:
                pix = QPixmap(full_path)
                if not pix.isNull():
                    self.preview.set_image(pix)
                    self._set_image_preview_meta(full_path, pix)
                    return
            # 图片路径失效：仍显示记录文案，不沿用其它记录的图
            self.preview.set_text_content(text)
            self.lbl_preview_meta.clear()
            self.lbl_preview_meta.setToolTip("")
            self.lbl_preview_meta.setVisible(False)
            return

        if kind == "folder":
            path, name = self._resolve_runlog_folder(current)
            self.preview.set_symbol_preview("📁", name or "文件夹")
            if path:
                self.lbl_preview_meta.setText(path)
                self.lbl_preview_meta.setToolTip(path)
                self.lbl_preview_meta.setVisible(True)
            else:
                self.lbl_preview_meta.clear()
                self.lbl_preview_meta.setToolTip("")
                self.lbl_preview_meta.setVisible(False)
            return

        if kind == "textfile":
            path, name = self._resolve_runlog_textfile(current)
            self.preview.set_symbol_preview("📄", name or "文本")
            if path:
                self.lbl_preview_meta.setText(path)
                self.lbl_preview_meta.setToolTip(path)
                self.lbl_preview_meta.setVisible(True)
            else:
                self.lbl_preview_meta.clear()
                self.lbl_preview_meta.setToolTip("")
                self.lbl_preview_meta.setVisible(False)
            return

        if kind == "fail":
            self.preview.set_symbol_preview("⚠️", "解析失败")
            self.lbl_preview_meta.clear()
            self.lbl_preview_meta.setToolTip("")
            self.lbl_preview_meta.setVisible(False)
            return

        # 其它操作/状态记录：显示原文
        self.preview.set_text_content(text)
        self.lbl_preview_meta.clear()
        self.lbl_preview_meta.setToolTip("")
        self.lbl_preview_meta.setVisible(False)

    # ────────────────────────────────────────
    # 对外接口（兼容旧版 server.py）
    # ────────────────────────────────────────
    @staticmethod
    def shared_queue(): return _SHARED_QUEUE
    def allow_accept(self): return False
    def drain_queue(self): pass

    def is_img_active(self):  return self.chk_imgonly.isChecked()
    def is_txt_active(self):  return self.chk_manual.isChecked()
    def is_mkdir_active(self): return self.chk_mkdir.isChecked()
    def is_any_active(self):  return self.is_img_active() or self.is_txt_active() or self.is_mkdir_active()
    def is_all_active(self):  return self.is_img_active() and self.is_txt_active() and self.is_mkdir_active()

    def _on_enable_all_toggled(self, checked: bool):
        """速存启用总开关：与左侧菜单 LED 等价，开/关全部子功能"""
        self.set_all_features(checked)

    def _sync_enable_all_display(self, _=None):
        """子功能变化时同步总开关的勾选显示（不触发信号循环）"""
        self.chk_enable_all.blockSignals(True)
        self.chk_enable_all.setChecked(self.is_any_active())
        self.chk_enable_all.blockSignals(False)

    def set_all_features(self, enabled: bool, quiet: bool = False):
        """一键开/关三个功能（供外部 LED / 偏好恢复调用）。

        quiet=True：只改状态与热键，不向「运行记录」写启用/关闭状态行
        （启动读 user.txt、总览保存后再次 apply 时用，避免同一套日志重复刷）。
        """
        self._block_switches(True)
        self.chk_mkdir.blockSignals(True)
        try:
            self.chk_imgonly.setChecked(enabled)
            self.chk_manual.setChecked(enabled)
            self.chk_mkdir.setChecked(enabled)
        finally:
            self._block_switches(False)
            self.chk_mkdir.blockSignals(False)
        # 手动触发各自逻辑（blockSignals 期间 toggled 不会发，须显式调用）
        self._on_imgonly_toggled(enabled, quiet=quiet)
        self._on_manual_toggled(enabled, quiet=quiet)
        self._on_mkdir_toggled(enabled, quiet=quiet)

    # ────────────────────────────────────────
    # 速存图片：触发逻辑
    # ────────────────────────────────────────
    def _update_nm_status(self):
        """后台检测插件连接状态，映射为三色灯 + 简短状态词（3-4字）。"""
        GREEN, RED, YELLOW = "#4caf50", "#e05252", "#f0a500"

        def _do_check():
            if self._closing:
                return
            try:
                ensure_ws_server()
            except Exception:
                _ws_log.exception("确保 WS 服务运行失败")
            if _ws_server_ok is False:
                self._set_plugin_ui(RED, "端口占用", "")
                return
            if _ws_server_ok is None:
                self._set_plugin_ui(YELLOW, "启动中", "")
                return

            secs = _ws_seconds_since_ping()
            has_sock = len(_ws_clients) > 0
            hb_text = f"{int(secs)}s前心跳" if secs < 9999 else ""

            if has_sock and secs < _WS_PING_TTL:
                self._set_plugin_ui(GREEN, "已连接", hb_text, bold=True)
                return
            if has_sock and secs >= _WS_PING_TTL:
                self._set_plugin_ui(YELLOW, "无心跳", f"{int(secs)}s无心跳")
                return
            if secs < 120:
                self._set_plugin_ui(YELLOW, "已断开", f"{int(secs)}s前最后心跳")
                return

            if _check_chrome_running():
                self._set_plugin_ui(YELLOW, "待连接", "")
            else:
                self._set_plugin_ui(RED, "未启动", "")

        if self._closing:
            return
        import threading as _th
        _th.Thread(target=_do_check, daemon=True).start()

    def _set_plugin_ui(self, color: str, chrome_status: str, heartbeat: str, bold: bool = False):
        """线程安全：通过信号把结果切回主线程更新（不能在子线程里直接碰 UI/定时器）。
        _closing 关闭了大部分竞态窗口；外层 try/except 是最后一道保险——
        万一检测线程正好卡在"判断完 _closing、还没来得及 emit"这一瞬间被关闭，
        C/C++ 侧对象已经没了，emit 会抛 RuntimeError，这里兜住，不让它冒到线程外面。"""
        if self._closing:
            return
        try:
            self.plugin_status_sig.emit(color, str(chrome_status or ""), str(heartbeat or ""), bool(bold))
        except RuntimeError:
            pass

    def _apply_status_btn_style(self, color: str, text: str, bold: bool = False):
        """给状态按钮刷色（参考「关于」弹窗中 Cookie 已加载的胶囊按钮）。"""
        weight = "600" if bold else "400"
        self.btn_status.setText(text)
        self.btn_status.setStyleSheet(
            f"QPushButton{{background:{color}; color:#ffffff;"
            f"border:1px solid {color}; border-radius:6px;"
            f"padding:2px 10px; font-weight:{weight}; font-size:12px;"
            f"min-height:22px; max-height:22px;}}"
            f"QPushButton:hover{{opacity:0.9;}}"
        )

    def _apply_plugin_ui(self, color: str, chrome_status: str, heartbeat: str, bold: bool):
        """在主线程真正更新状态按钮与心跳文字（由 plugin_status_sig 触发）。"""
        self._apply_status_btn_style(color, chrome_status, bold)
        hb = heartbeat or "——"
        self.lbl_heartbeat.setStyleSheet(
            f"background: transparent; color: {color}; font-size: 14px;"
        )
        self.lbl_heartbeat.setText(hb)


    # ────────────────────────────────────────

    # ────────────────────────────────────────
    # 速存图片：开关与设置变更
    # ────────────────────────────────────────
    def _on_imgonly_toggled(self, checked: bool, quiet: bool = False):
        """速存图片开关。quiet=True 时不写启用/关闭/诊断状态行（偏好恢复用）。"""
        if checked:
            # 偏好恢复时勿弹路径对话框
            if not self._require_writable_path(interactive=not quiet):
                self._block_switches(True)
                self.chk_imgonly.setChecked(False)
                self._block_switches(False)
                _ws_set_enabled(False)          # 路径不可用 → 通知插件停用
                # 路径问题仍写入记录；quiet 下同类只记一次，避免反复 apply 刷屏
                msg = "⚠️ 保存路径不可用，已取消速存图片"
                if quiet:
                    self._runlog_once("path_unusable", msg)
                else:
                    self._log_problem(msg)
                return

            if not quiet:
                self._diagnose()                # 用户手动启用：诊断一行，问题一眼可见
            self._prime_seen_and_quiet()        # 处理暂存区已有的图片
            self._imgonly_timer.start()

            self.state = self.STATE_IMGSAVE
            _ws_set_enabled(True)               # 通知插件：可以开始存图
            if not quiet:
                self.list_widget.addItem(
                    "🟢 速存图片：已启用（在浏览器里把鼠标停在图片上按 Alt+1）"
                )
                self.list_widget.addItem(
                    f"📁 图片将归档到：{self.save_path.text().strip()}"
                )
        else:
            self._imgonly_timer.stop()
            self.state = self.STATE_IDLE
            _ws_set_enabled(False)              # 通知插件：停止存图
            if not quiet:
                self.list_widget.addItem("⏹️ 速存图片：已关闭")

    def _on_img_hotkey_changed(self, key: str):
        # 快捷键固定为 Alt+1，不再需要与浏览器同步，这里保留空实现以兼容信号连接
        pass



    # ────────────────────────────────────────
    # 目录监控：显示插件保存的新文件
    # ────────────────────────────────────────
    def _get_plugin_save_dir(self) -> str:
        """插件把图片存到：系统下载目录/WebImageSaver"""
        from PyQt5.QtCore import QStandardPaths
        dl = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
        if not dl:
            dl = os.path.join(os.path.expanduser("~"), "Downloads")
        return os.path.join(dl, "WebImageSaver")

    _IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".avif", ".ico"}

    def _scan_imgonly_files(self):
        """每秒扫描浏览器暂存目录（下载目录/WebImageSaver）：
        - 若保存路径与暂存目录不同 → 把新图移动过去并记录；
        - 若保存路径就等于暂存目录 → 图片本就在此，直接记录（不搬运）。"""
        try:
            self._archive_new(log=True)
        except Exception as e:
            self._log_problem(f"❌ 图片归档错误：{e}")

    @staticmethod
    def _file_sig(path):
        st = os.stat(path)
        return (os.path.basename(path), st.st_size, int(st.st_mtime))

    def _archive_new(self, log: bool = True):
        staging = self._get_plugin_save_dir()
        if not os.path.isdir(staging):
            return
        target = self.save_path.text().strip()
        if not target:
            return
        try:
            os.makedirs(target, exist_ok=True)
        except Exception as e:
            if log:
                self._log_problem(f"❌ 保存路径不可用：{target}（{e}）")
            return

        same_dir = os.path.abspath(staging) == os.path.abspath(target)
        for name in sorted(os.listdir(staging)):
            if name.endswith(".crdownload"):
                continue
            if os.path.splitext(name)[1].lower() not in self._IMG_EXTS:
                continue
            src = os.path.join(staging, name)
            if not os.path.isfile(src):
                continue
            try:
                sig = self._file_sig(src)
            except OSError:
                continue   # 文件可能刚被移走/占用，下一轮再看

            if sig in self._archived_seen:
                # 这张已经处理过；若是搬运模式，上次可能“复制成功但删除失败”，顺手清一下暂存副本
                if not same_dir:
                    try: os.remove(src)
                    except OSError: pass
                continue

            if same_dir:
                # 目标即暂存区：不搬运，只记录
                self._archived_seen.add(sig)
                if log:
                    self._push_record(
                        f"✅ 已保存图片：{name}", select=True, filepath=src
                    )
            else:
                # 跨目录：先复制（成功即代表图片已安全落到目标），记录后再删暂存副本
                dst = self._unique_path(os.path.join(target, name))
                try:
                    shutil.copy2(src, dst)
                except (PermissionError, OSError):
                    continue   # 文件可能仍在写入或被占用，下一轮重试（不记录、不标记）
                self._archived_seen.add(sig)   # 复制成功即标记，避免重复
                if log:
                    self._push_record(
                        f"✅ 已保存图片：{os.path.basename(dst)}",
                        select=True,
                        filepath=dst,
                    )
                try:
                    os.remove(src)             # 再删暂存副本；删不掉也没关系，下一轮会再清
                except OSError:
                    pass

    @staticmethod
    def _unique_path(path: str) -> str:
        if not os.path.exists(path):
            return path
        base, ext = os.path.splitext(path)
        i = 1
        while os.path.exists(f"{base} ({i}){ext}"):
            i += 1
        return f"{base} ({i}){ext}"

    def _prime_seen_and_quiet(self, scan_dir=None):
        """启用/切换保存路径时调用：
        - 目标≠暂存区 → 把暂存区已有的图片搬过去并记录；
        - 目标==暂存区 → 把已有文件标记为已知（不刷屏），之后只记录新图。"""
        staging = self._get_plugin_save_dir()
        target = self.save_path.text().strip()
        try:
            same_dir = bool(target) and os.path.isdir(staging) \
                and os.path.abspath(staging) == os.path.abspath(target)
        except Exception:
            same_dir = False
        if same_dir:
            self._archived_seen = set()
            try:
                for n in os.listdir(staging):
                    p = os.path.join(staging, n)
                    if os.path.isfile(p):
                        try: self._archived_seen.add(self._file_sig(p))
                        except OSError: pass
            except Exception:
                log.exception("扫描速存暂存区失败 staging=%s", staging)
        else:
            try:
                self._archive_new(log=True)
            except Exception:
                log.exception("归档速存新文件失败")

    def _log_problem(self, msg: str):
        """诊断/连接/错误类信息写入运行记录列表，自动拆行逐条追加并滚屏。"""
        try:
            for line in str(msg or "").split("\n"):
                if line.strip():
                    self.list_widget.addItem(line.strip())
            self.list_widget.scrollToBottom()
        except Exception:
            log.exception("写入速存运行记录失败 msg=%s", str(msg or "")[:200])

    def _runlog_once(self, key: str, msg: str) -> bool:
        """运行记录按 key 去重：同一 key 本会话只写一条（偏好 quiet 恢复用）。

        返回 True 表示本次已写入；False 表示此前已写过、已跳过。
        """
        keys = getattr(self, "_runlog_once_keys", None)
        if keys is None:
            self._runlog_once_keys = set()
            keys = self._runlog_once_keys
        if key in keys:
            return False
        keys.add(key)
        try:
            self.list_widget.addItem(msg)
            self.list_widget.scrollToBottom()
        except Exception:
            pass
        return True

    def _diagnose(self):
        """点击按钮时在运行记录中输出一行精简诊断。"""
        port = (
            "正常" if _ws_server_ok is True
            else "占用" if _ws_server_ok is False else "启动中"
        )
        conn = "已连接" if _ws_connected() else "未连接"
        on_off = "开" if self.chk_imgonly.isChecked() else "关"
        self._log_problem(f"🔧 端口{port} | 插件{conn} | 速存已{on_off}")

    # ────────────────────────────────────────
    # 速存文本
    # ────────────────────────────────────────
    def _current_txt_key(self) -> str:
        for key, rb in self._txt_radio.items():
            if rb.isChecked():
                return key
        return "F4"

    def _on_txt_hotkey_changed(self, _=None):
        key = self._current_txt_key()
        if hasattr(self, "_sc_manual"):
            self._sc_manual.setKey(QKeySequence(key))
        # 无论是否启用，都在「运行记录」留一条改键记录
        self.list_widget.addItem(f"⌨️ 文本快捷键已切换为：{key}")
        self.list_widget.scrollToBottom()
        # 仅在已启用时才真正重注册全局热键
        if self.chk_manual.isChecked():
            self._register_txt_hotkey(announce=False)

    def _on_manual_toggled(self, checked: bool, quiet: bool = False):
        """速存文本开关。quiet=True 时不写启用/关闭状态行。"""
        if checked:
            self._sc_manual.setEnabled(True)
            self._register_txt_hotkey(announce=False, quiet=quiet)
            if not quiet:
                self.list_widget.addItem(
                    f"🟡 速存文本：已启用（按 {self._current_txt_key()} 在资源管理器创建 .txt）"
                )
        else:
            self._sc_manual.setEnabled(False)
            self._unregister_txt_hotkey()
            if not quiet:
                self.list_widget.addItem("⏹️ 速存文本：已关闭")

    def _retry_hotkey_later(self, fn, attempt: int, tag: str) -> bool:
        """RegisterHotKey 失败后延迟再试。attempt 是刚刚失败的那一次（从 0 起）。"""
        if getattr(self, "_closing", False):
            return False
        if attempt >= len(_HK_RETRY_MS):
            return False
        delay = int(_HK_RETRY_MS[attempt])
        log.info("热键稍后重试 tag=%s attempt=%s delay_ms=%s", tag, attempt + 1, delay)
        QTimer.singleShot(delay, fn)
        return True

    def _register_txt_hotkey(
        self, announce: bool = True, quiet: bool = False, _attempt: int = 0
    ) -> bool:
        if getattr(self, "_closing", False):
            return False
        if _attempt and not self.chk_manual.isChecked():
            return False
        key = self._current_txt_key()
        if getattr(self, "_hotkey_registered_key", None) == key:
            return True
        self._unregister_txt_hotkey()
        vk = VK_CODE.get(key, VK_CODE["F4"])
        try:
            ok, err = _win_register_hotkey(self._hotkey_id, vk)
        except Exception as e:
            msg = f"❌ 注册热键 {key} 异常：{e}"
            if quiet:
                self._runlog_once(f"txt_hk_exc:{key}", msg)
            else:
                self.list_widget.addItem(msg)
            self._hotkey_registered_key = None
            return False
        if not ok:
            extra = f"（错误码 {err}）" if err else ""
            retrying = self._retry_hotkey_later(
                lambda a=_attempt + 1: self._register_txt_hotkey(
                    announce=False, quiet=True, _attempt=a
                ),
                _attempt,
                f"txt:{key}",
            )
            if _attempt == 0:
                msg = f"❌ 注册热键 {key} 失败，可能被其它程序占用{extra}"
                if retrying:
                    msg += "，稍后重试"
                if quiet:
                    self._runlog_once(f"txt_hk_fail:{key}", msg)
                else:
                    self.list_widget.addItem(msg)
            elif not retrying:
                log.warning("注册热键 %s 多次失败 winerr=%s", key, err)
            self._hotkey_registered_key = None
            return False
        if self._hotkey_filter is None:
            self._hotkey_filter = _GlobalHotkeyFilter(self.manual_fast_save, self._hotkey_id)
            QCoreApplication.instance().installNativeEventFilter(self._hotkey_filter)
        self._hotkey_registered_key = key
        if _attempt > 0:
            self.list_widget.addItem(f"🟡 热键 {key} 已就绪")
            self.list_widget.scrollToBottom()
        elif announce and not quiet:
            self.list_widget.addItem(f"🟡 速存文本全局热键就绪：{key}")
        return True

    def _unregister_txt_hotkey(self):
        try:
            _win_unregister_hotkey(self._hotkey_id)
        except Exception:
            # 未注册过 / 系统调用失败等情况都不应影响页面正常构造与运行
            log.debug("注销速存热键失败", exc_info=True)
        finally:
            self._hotkey_registered_key = None

    def manual_fast_save(self):
        if not self.chk_manual.isChecked():
            self.list_widget.addItem("⚠️ 请先启用速存文本")
            return
        if not _WIN_OK:
            self.list_widget.addItem("❌ 手动功能不可用：缺少 pywin32/pyperclip")
            return
        try:
            # ── 第一步：只取"前台激活"的资源管理器窗口 ──────────────
            fg_hwnd = win32gui.GetForegroundWindow()
            if not fg_hwnd:
                self.list_widget.addItem("⚠️ 无法获取前台窗口，请先点击资源管理器再按快捷键")
                return

            doc, n_tabs = self._resolve_active_view(fg_hwnd, prefer_selected=True)

            if doc is None and n_tabs == 0:
                self.list_widget.addItem("⚠️ 前台窗口不是资源管理器，请先切换到资源管理器再按快捷键")
                return
            if doc is None:
                self.list_widget.addItem(
                    f"⚠️ 未选中任何文件，请先选中一个文件再按快捷键（检测到 {n_tabs} 个标签）"
                )
                return

            # ── 第二步：获取选中项，限制为"恰好 1 个文件" ────────────
            try:
                sel = doc.SelectedItems()
                count = sel.Count
            except Exception:
                self.list_widget.addItem("❌ 无法读取选中项")
                return

            if count == 0:
                self.list_widget.addItem("⚠️ 未选中任何文件，请先选中一个文件再按快捷键")
                return
            if count > 1:
                self.list_widget.addItem(f"⚠️ 选中了 {count} 个项目，每次只能对 1 个文件操作，请重新选择")
                return

            item = sel.Item(0)
            target_path = item.Path

            # 排除文件夹
            if os.path.isdir(target_path):
                self.list_widget.addItem(f"⚠️ 选中的是文件夹，不支持对文件夹操作：{os.path.basename(target_path)}")
                return

            # ── 第三步：读取剪贴板 ───────────────────────────────────
            clip_text = ""
            try:
                clip_text = pyperclip.paste().strip()
            except Exception:
                log.debug("读取剪贴板失败", exc_info=True)

            self.list_widget.addItem(
                f"📋 剪贴板{'有内容' if clip_text else '无内容（将写入空文件）'}"
            )

            # ── 第四步：写入同名 .txt ────────────────────────────────
            txt_path = os.path.splitext(target_path)[0] + ".txt"
            # 防重复：目标文件已存在且内容与剪贴板完全一致 → 视为误触发重复，直接忽略
            if self._txt_already_saved(txt_path, clip_text):
                self.list_widget.addItem(
                    f"⏭️ 已忽略重复记录：{os.path.basename(txt_path)} 内容与已保存相同"
                )
                self.list_widget.scrollToBottom()
                return
            try:
                with open(txt_path, "w", encoding="utf-8") as fp:
                    fp.write(clip_text)
                self._push_record(
                    f"✅ 速存文本成功：{os.path.basename(txt_path)}",
                    select=True,
                    filepath=txt_path,
                )
            except Exception as e:
                log.exception("速存文本写入失败 path=%s", txt_path)
                self.list_widget.addItem(f"❌ 写入失败：{txt_path}（{e}）")

            self.list_widget.scrollToBottom()
        except Exception as e:
            log.exception("速存文本失败")
            self.list_widget.addItem(f"❌ 速存文本错误：{e}")

    def _txt_already_saved(self, txt_path: str, content: str) -> bool:
        """目标 .txt 已存在且内容与当前内容完全一致 → 视为重复记录。"""
        try:
            if not os.path.isfile(txt_path):
                return False
            with open(txt_path, "r", encoding="utf-8") as fp:
                return fp.read() == content
        except Exception:
            return False

    # ────────────────────────────────────────
    # 公共工具
    # ────────────────────────────────────────
    def _enter_idle(self, reset_switches=False):
        if reset_switches:
            self._block_switches(True)
            try:
                self.chk_imgonly.setChecked(False)
                self.chk_manual.setChecked(False)
            finally:
                self._block_switches(False)
        try:
            self._sc_manual.setEnabled(False)
        except Exception:
            pass
        self.state = self.STATE_IDLE
        self._unregister_txt_hotkey()
        for h in [self._imgonly_timer]:
            try:
                if hasattr(h, "stop"): h.stop()
            except Exception:
                pass

    def _block_switches(self, yes: bool):
        for w in [self.chk_imgonly, self.chk_manual]:
            try: w.blockSignals(yes)
            except Exception: pass

    def _append_log(self, text: str):
        try:
            self.list_widget.addItem(text)
            self.list_widget.scrollToBottom()
        except Exception:
            pass

    def _push_record(self, text: str, select: bool = False, filepath: str = None):
        """速存操作成功时统一调用：追加运行记录 → 滚到底部显示最新 →
        选中该条（联动右侧预览）→ 请求主窗口切到「速存图文」页。
        自动去重：同一文件名不会重复记录「已保存图片」。
        filepath：本地完整路径（图片文件 / 文件夹 / 文本文件均可），
        写入条目 UserRole 供预览区稳定加载。"""
        # 去重：同文件名只记一次（防 WS 下载 + 目录监控重复记录）
        m = self._IMG_LOG_RE.search(text)
        if m:
            fn = m.group(1).strip().lower()
            if fn and fn in self._saved_filenames:
                return  # 已记录过，跳过重复条目
            if fn:
                self._saved_filenames.add(fn)
        try:
            item = QListWidgetItem(text)
            path = (filepath or "").strip()
            if path and (os.path.isfile(path) or os.path.isdir(path)):
                path = os.path.abspath(path)
                item.setData(Qt.UserRole, path)
            elif m:
                # 未显式传入时，尽量用当前保存目录拼出路径
                guess = os.path.join(
                    (self.save_path.text() or "").strip(), m.group(1).strip()
                )
                if os.path.isfile(guess):
                    guess = os.path.abspath(guess)
                    item.setData(Qt.UserRole, guess)
            self.list_widget.addItem(item)
            # addItem 已：选中最新 + 滚屏；select 参数保留语义兼容
            if select:
                self.list_widget._focus_latest(force_select=True)
        except Exception:
            pass
        self.request_show.emit()

    # ── WS SAVE 运行记录：解析中（黄灯）→ 完成/失败 ──
    def _on_parse_start(self, url: str):
        """主线程：收到插件 SAVE 命令，切到本页并在运行记录添加「解析中」条目。

        新记录事件：选中该条（空预览）并滚屏到最新。
        """
        self.request_show.emit()
        short = url[:60] + "…" if len(url) > 60 else url
        item = QListWidgetItem(f"🟡 解析中… {short}")
        item.setData(Qt.UserRole, url)
        self.list_widget.addItem(item)  # 内部已选中最新 + 滚屏
        self._parsing_items[url] = item

    def _on_parse_done(self, url: str, filepath: str, success: bool):
        """主线程：下载完成，移除「解析中」条目，以 _push_record 方式添加结果（统一走去重）。
        filepath：成功时为本地完整路径，失败时可能为空。
        成功后选中「已保存」并滚屏；失败同理。
        """
        item = self._parsing_items.pop(url, None)
        if item is not None:
            row = self.list_widget.row(item)
            if row >= 0:
                self.list_widget.takeItem(row)
        path = (filepath or "").strip()
        if success and path:
            name = os.path.basename(path)
            self._push_record(
                f"✅ 已保存图片：{name}", select=True, filepath=path
            )
        else:
            fail_hint = os.path.basename(path) if path else (url[:60] if url else "")
            fail_item = QListWidgetItem(f"❌ 解析失败：{fail_hint}")
            self.list_widget.addItem(fail_item)  # 内部已选中最新 + 滚屏

    def _init_save_dir(self):
        # 仅设默认路径；不写运行记录——随后 apply_settings 会恢复用户路径，
        # 写「初始路径」容易与真实归档目录不一致，且造成启动噪声。
        path = self._resolve_default_dir()
        self.save_path.setText(path)

    def _resolve_default_dir(self) -> str:
        candidates = []
        pic = QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)
        if pic: candidates.append(os.path.join(pic, "WebImageSaver"))
        home = os.path.expanduser("~") or ""
        if home: candidates.append(os.path.join(home, "WebImageSaver"))
        candidates.append(os.path.join(os.getcwd(), "WebImageSaver"))
        for p in candidates:
            ok, norm, _ = self._ensure_writable_dir(p, create=True)
            if ok: return norm
        folder = QFileDialog.getExistingDirectory(self, "选择保存文件夹")
        if folder:
            ok, norm, _ = self._ensure_writable_dir(folder, create=True)
            if ok: return norm
        fallback = os.path.join(home or os.getcwd(), "WebImageSaver")
        os.makedirs(fallback, exist_ok=True)
        return os.path.abspath(fallback)

    def _ensure_writable_dir(self, path, create=True):
        try:
            if not path: return False, "", "空路径"
            if create: os.makedirs(path, exist_ok=True)
            tf = os.path.join(path, "__w_test__.tmp")
            with open(tf, "w") as f: f.write("ok")
            os.remove(tf)
            return True, os.path.abspath(path), ""
        except Exception as e:
            return False, path, str(e)

    def _require_writable_path(self, interactive=True) -> bool:
        path = self.save_path.text().strip()
        ok, norm, err = self._ensure_writable_dir(path, create=True)
        if ok:
            self.save_path.setStyleSheet("")
            if norm != path: self.save_path.setText(norm)
            return True
        self.save_path.setStyleSheet(f"border: 1px solid {tk('err')};")
        if not interactive:
            return False
        if path: self.list_widget.addItem(f"⚠️ 路径不可用：{path}（{err}）")
        while True:
            folder = QFileDialog.getExistingDirectory(self, "选择保存文件夹")
            if not folder: return False
            ok2, norm2, err2 = self._ensure_writable_dir(folder, create=True)
            if ok2:
                self.save_path.setStyleSheet("")
                self.save_path.setText(norm2)
                self._prime_seen_and_quiet(norm2)
                return True
            self.list_widget.addItem(f"❌ 该目录不可写：{folder}（{err2}），请重新选择")

    def _on_path_edited(self, _=None):
        _ws_set_save_dir(self.save_path.text().strip())
        self._require_writable_path(interactive=False)

    def _choose_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "选择保存文件夹")
        if not folder: return
        self.save_path.setText(folder)
        if self._require_writable_path(interactive=True):
            path = self.save_path.text().strip()
            self.list_widget.addItem(f"📁 保存路径已改为：{path}")
            self.list_widget.scrollToBottom()
            self._prime_seen_and_quiet(path)

    # ────────────────────────────────────────
    # 生命周期
    # ────────────────────────────────────────
    def hideEvent(self, e):
        try: self._sc_manual.setEnabled(False)
        finally: super().hideEvent(e)

    def showEvent(self, e):
        if not hasattr(self, "_container"):
            self._bind_container_switch()
        super().showEvent(e)
        if self.chk_manual.isChecked():
            self._sc_manual.setEnabled(True)
            self._register_txt_hotkey(announce=False)

    # ────────────────────────────────────────
    # 速建文件夹
    # ────────────────────────────────────────
    @staticmethod
    def _next_folder_name(name: str) -> str:
        """
        推算下一个文件夹名：自动定位名称中的数字段并加 1，保留其余文字与位数。
          纯数字            511      → 512
          数字在结尾        H04      → H05 ；记录8 → 记录9
          数字在开头        5单元    → 6单元
          数字在中间        第2章    → 第3章
          含多段数字        2024第1季 → 2024第2季（默认对最后一段数字加 1）
          完全不含数字      服装     → 服装002
        位数处理：进位不足则补零保持原宽度（04→05）；超出原宽度则不截断（99→100，第9章→第10章）。
        """
        matches = list(re.finditer(r'\d+', name))
        if not matches:
            # 完全没有数字：追加 002 作为起始序号
            return name + "002"
        m = matches[-1]                       # 默认对最后一段数字加 1
        num_str = m.group(0)
        next_str = str(int(num_str) + 1).zfill(len(num_str))  # zfill 只补零、超宽不截断
        return name[:m.start()] + next_str + name[m.end():]

    @staticmethod
    def _next_available_folder_name(parent_dir: str, name: str) -> str:
        """
        目录感知的顺序新建：不是简单对选中名 +1，而是扫描同级目录里所有
        「同前缀 + 数字段 + 同后缀」的文件夹，取其中最大编号再 +1，从而自动跳过
        已存在的编号，一次建到位。
          选中 09，目录已有 10、11            → 12
          选中 H16B，目录已有 H15B/H16B/H17B → H18B
        位宽沿用选中名数字段的宽度（zfill 补零、超宽不截断）；若结果仍意外存在则
        继续 +1 直到空位。名称完全不含数字时，退回 name + 三位序号 起始。
        """
        matches = list(re.finditer(r'\d+', name))
        if not matches:
            # 无数字：从 name002 起向后找第一个空位
            n, width = 2, 3
            while True:
                cand = f"{name}{str(n).zfill(width)}"
                if not os.path.exists(os.path.join(parent_dir, cand)):
                    return cand
                n += 1

        m = matches[-1]                       # 定位最后一段数字
        prefix, suffix = name[:m.start()], name[m.end():]
        width  = len(m.group(0))
        max_num = int(m.group(0))             # 起点至少是选中名本身的编号

        # 扫描同级目录里「同前缀、同后缀」的文件夹，取最大编号
        pat = re.compile(r'^' + re.escape(prefix) + r'(\d+)' + re.escape(suffix) + r'$')
        try:
            for entry in os.listdir(parent_dir):
                if os.path.isdir(os.path.join(parent_dir, entry)):
                    mm = pat.match(entry)
                    if mm:
                        max_num = max(max_num, int(mm.group(1)))
        except OSError:
            pass

        # 从 max_num+1 起，找第一个不存在的编号
        n = max_num + 1
        while True:
            cand = prefix + str(n).zfill(width) + suffix
            if not os.path.exists(os.path.join(parent_dir, cand)):
                return cand
            n += 1

    def _current_mkdir_key(self) -> str:
        """当前顺序新建所选热键（F5-F8），默认 F8。"""
        for key, rb in self._mkdir_radio.items():
            if rb.isChecked():
                return key
        return "F8"

    def _register_mkdir_seq(self, announce: bool = True, _attempt: int = 0) -> bool:
        """注册（或按所选键重注册）顺序新建热键。

        已成功注册且键未变时直接返回 True，避免再次 apply 时
        Unregister→Register 竞态或被占用时反复失败刷屏。
        """
        if getattr(self, "_closing", False):
            return False
        if _attempt and not self.chk_mkdir.isChecked():
            return False
        seq_key = self._current_mkdir_key()
        if (
            getattr(self, "_mkdir_hotkey_registered", False)
            and getattr(self, "_mkdir_hotkey_key", None) == seq_key
        ):
            return True
        _win_unregister_hotkey(self._mkdir_hotkey_id)
        try:
            ok, err = _win_register_hotkey(self._mkdir_hotkey_id, VK_CODE[seq_key])
        except Exception:
            ok, err = False, -1
        if not ok:
            self._mkdir_hotkey_registered = False
            self._mkdir_hotkey_key = None
            retrying = self._retry_hotkey_later(
                lambda a=_attempt + 1: self._register_mkdir_seq(
                    announce=False, _attempt=a
                ),
                _attempt,
                f"mkdir:{seq_key}",
            )
            if _attempt == 0:
                log.warning(
                    "注册顺序新建热键失败 key=%s winerr=%s retrying=%s",
                    seq_key, err, retrying,
                )
            return False
        if self._mkdir_hotkey_filter is None:
            self._mkdir_hotkey_filter = _GlobalHotkeyFilter(
                self.mkdir_fast_create, self._mkdir_hotkey_id
            )
            QCoreApplication.instance().installNativeEventFilter(self._mkdir_hotkey_filter)
        self._mkdir_hotkey_registered = True
        self._mkdir_hotkey_key = seq_key
        if _attempt > 0:
            self.list_widget.addItem(f"🟡 热键 {seq_key} 已就绪")
            self.list_widget.scrollToBottom()
        elif announce:
            self.list_widget.addItem(f"⌨️ 顺序新建热键切换为：{seq_key}")
        return True

    def _on_mkdir_hotkey_changed(self, _=None):
        """切换顺序新建可选键：始终记录一条；仅已启用时才真正重注册热键。"""
        seq_key = self._current_mkdir_key()
        self.list_widget.addItem(f"⌨️ 顺序新建热键已切换为：{seq_key}")
        self.list_widget.scrollToBottom()
        if self.chk_mkdir.isChecked():
            self._register_mkdir_seq(announce=False)

    def _on_mkdir_toggled(self, checked: bool, quiet: bool = False):
        """速建文件夹开关。quiet=True 时不写启用/关闭状态行；热键失败最多记一次。"""
        if checked:
            seq_key = self._current_mkdir_key()
            ok_seq = self._register_mkdir_seq(announce=False)
            if not ok_seq:
                msg = f"❌ 注册 {seq_key} 热键失败，可能被其他程序占用，稍后重试"
                if quiet:
                    self._runlog_once(f"mkdir_seq_fail:{seq_key}", msg)
                else:
                    self.list_widget.addItem(msg)
                # F8 失败不挡住 F9/F10；顺序键会自己延迟再注册

            # F9 / F10：先 Unregister 再 Register，保证关闭后再开 / 再次 apply 均幂等
            for _hid in (self._mkdir_direct_id, self._mkdir_direct_b_id):
                _win_unregister_hotkey(_hid)

            # 注册 F9「直接新建 A」
            try:
                ok9, _err9 = _win_register_hotkey(self._mkdir_direct_id, VK_CODE["F9"])
            except Exception:
                ok9 = False
            self._mkdir_direct_registered = bool(ok9)
            if ok9 and self._mkdir_direct_filter is None:
                self._mkdir_direct_filter = _GlobalHotkeyFilter(
                    self.mkdir_direct_create_a, self._mkdir_direct_id
                )
                QCoreApplication.instance().installNativeEventFilter(self._mkdir_direct_filter)

            # 注册 F10「直接新建 B」
            try:
                ok10, _err10 = _win_register_hotkey(self._mkdir_direct_b_id, VK_CODE["F10"])
            except Exception:
                ok10 = False
            self._mkdir_direct_b_registered = bool(ok10)
            if ok10 and self._mkdir_direct_b_filter is None:
                self._mkdir_direct_b_filter = _GlobalHotkeyFilter(
                    self.mkdir_direct_create_b, self._mkdir_direct_b_id
                )
                QCoreApplication.instance().installNativeEventFilter(self._mkdir_direct_b_filter)

            if quiet:
                if not (ok9 and ok10):
                    failed = " ".join(k for k, ok in (("F9", ok9), ("F10", ok10)) if not ok)
                    self._runlog_once(
                        f"mkdir_direct_fail:{failed}",
                        f"⚠️ 速建文件夹：{failed} 热键注册失败（可能被占用）",
                    )
            elif ok9 and ok10:
                self.list_widget.addItem(
                    f"🟢 速建文件夹：已启用（{seq_key} 顺序新建 / F9 · F10 直接新建 就绪）"
                )
            else:
                failed = " ".join(k for k, ok in (("F9", ok9), ("F10", ok10)) if not ok)
                self.list_widget.addItem(
                    f"🟢 速建文件夹：{seq_key} 已启用；⚠️ {failed} 注册失败（可能被占用）"
                )
        else:
            self._unregister_mkdir_hotkey()
            if not quiet:
                self.list_widget.addItem("⏹️ 速建文件夹：已关闭")

    def _register_recording_hotkey(self, _attempt: int = 0):
        """注册 F6 全局热键：按下切换记录模式。"""
        if getattr(self, "_closing", False):
            return False
        _win_unregister_hotkey(self._recording_hotkey_id)
        try:
            ok, err = _win_register_hotkey(self._recording_hotkey_id, VK_CODE["F6"])
        except Exception:
            ok, err = False, -1
        if not ok:
            retrying = self._retry_hotkey_later(
                lambda a=_attempt + 1: self._register_recording_hotkey(_attempt=a),
                _attempt,
                "rec:F6",
            )
            if _attempt == 0:
                log.warning("注册 F6 热键失败 winerr=%s retrying=%s", err, retrying)
            self._recording_hotkey_filter = None
            self._recording_hotkey_registered = False
            return False
        if self._recording_hotkey_filter is None:
            self._recording_hotkey_filter = _GlobalHotkeyFilter(
                self._toggle_record_mode, self._recording_hotkey_id
            )
            QCoreApplication.instance().installNativeEventFilter(self._recording_hotkey_filter)
        self._recording_hotkey_registered = True
        if _attempt > 0:
            log.info("F6 热键重试成功 attempt=%s", _attempt)
        return True

    def _unregister_recording_hotkey(self):
        _win_unregister_hotkey(self._recording_hotkey_id)
        self._recording_hotkey_filter = None
        self._recording_hotkey_registered = False

    def _block_mkdir(self, yes: bool):
        try: self.chk_mkdir.blockSignals(yes)
        except Exception: pass

    def _unregister_mkdir_hotkey(self):
        _win_unregister_hotkey(self._mkdir_hotkey_id)
        _win_unregister_hotkey(self._mkdir_direct_id)
        _win_unregister_hotkey(self._mkdir_direct_b_id)
        self._mkdir_hotkey_registered = False
        self._mkdir_hotkey_key = None
        self._mkdir_direct_registered = False
        self._mkdir_direct_b_registered = False

    def hotkey_inventory(self):
        """配置页清单：速存相关全局热键及当前占用状态。"""
        def row(name, key, state):
            return {"group": "速存图文", "name": name, "key": key, "state": state}

        rows = [row("速存图片", "Alt+1", "plugin")]
        txt_key = self._current_txt_key()
        if not self.chk_manual.isChecked():
            txt_st = "off"
        elif getattr(self, "_hotkey_registered_key", None) == txt_key:
            txt_st = "ok"
        else:
            txt_st = "busy"
        rows.append(row("速存文本", txt_key, txt_st))
        rec_st = "ok" if getattr(self, "_recording_hotkey_registered", False) else "busy"
        rows.append(row("记录 / 批处理中止", "F6", rec_st))
        mk = self._current_mkdir_key()
        if not self.chk_mkdir.isChecked():
            rows.append(row("顺序新建文件夹", mk, "off"))
            rows.append(row("直接新建 A", "F9", "off"))
            rows.append(row("直接新建 B", "F10", "off"))
        else:
            rows.append(row(
                "顺序新建文件夹", mk,
                "ok" if getattr(self, "_mkdir_hotkey_registered", False) else "busy",
            ))
            rows.append(row(
                "直接新建 A", "F9",
                "ok" if getattr(self, "_mkdir_direct_registered", False) else "busy",
            ))
            rows.append(row(
                "直接新建 B", "F10",
                "ok" if getattr(self, "_mkdir_direct_b_registered", False) else "busy",
            ))
        return rows

    def rebind_hotkeys(self):
        """注销后按当前开关重注册。返回 [(名称, 按键, 成功), ...]。"""
        out = []
        ok_f6 = bool(self._register_recording_hotkey())
        out.append(("记录 / 批处理中止", "F6", ok_f6))
        if self.chk_manual.isChecked():
            self._hotkey_registered_key = None
            key = self._current_txt_key()
            ok = bool(self._register_txt_hotkey(announce=False, quiet=True))
            out.append(("速存文本", key, ok))
        if self.chk_mkdir.isChecked():
            self._mkdir_hotkey_registered = False
            self._mkdir_hotkey_key = None
            self._on_mkdir_toggled(True, quiet=True)
            mk = self._current_mkdir_key()
            out.append((
                "顺序新建文件夹", mk,
                bool(getattr(self, "_mkdir_hotkey_registered", False)),
            ))
            out.append((
                "直接新建 A", "F9",
                bool(getattr(self, "_mkdir_direct_registered", False)),
            ))
            out.append((
                "直接新建 B", "F10",
                bool(getattr(self, "_mkdir_direct_b_registered", False)),
            ))
        return out

    # ────────────────────────────────────────
    # 资源管理器（含 Win10/11 多标签）活动视图解析
    # ────────────────────────────────────────

    @staticmethod
    def _view_path(doc) -> str:
        """取某个文件夹视图当前所在目录，失败返回空串。

        注意：不能用 callable() 来区分「属性」和「方法」——
        pywin32 的 COM 对象实现了 __call__（默认属性），callable() 恒为真，
        误判会导致把属性当方法调用而取不到路径。这里改为直接按顺序试。
        """
        # 路径 1：pywin32（ShellFolderView）——属性式访问
        try:
            p = doc.Folder.Self.Path
            if p:
                return p
        except Exception:
            pass
        # 路径 2：comtypes dynamic 包装——方法式访问
        try:
            p = doc.Folder().Self().Path
            if p:
                return p
        except Exception:
            pass
        return ""

    @staticmethod
    def _children_by_zorder(hwnd):
        """按 z-order（从最顶层开始）列出 hwnd 的直接子窗口。"""
        import win32con
        res = []
        try:
            h = win32gui.GetWindow(hwnd, win32con.GW_CHILD)   # z-order 最顶的子窗口
            guard = 0
            while h and guard < 256:
                res.append(h)
                h = win32gui.GetWindow(h, win32con.GW_HWNDNEXT)
                guard += 1
        except Exception:
            pass
        return res

    def _active_tab_hwnd(self, fg_hwnd):
        """取「当前活动标签」的控件窗口句柄（Win11 标签式资源管理器）。

        依据：每个标签都有自己的 ShellTabWindowClass 控件窗口，
        而【活动标签的控件窗口始终位于 z-order 最顶端】。
        注意：不能用「窗口是否可见」来判断 —— 对 Win11 资源管理器无效，
        非活动标签的窗口同样报告为可见（这正是之前几版失败的原因）。

        无标签（Win10 原生）时返回 0，调用方直接用主窗口即可。
        """
        for h in self._children_by_zorder(fg_hwnd):
            try:
                if win32gui.GetClassName(h) == "ShellTabWindowClass":
                    return h          # 第一个即 z-order 最顶 = 活动标签
            except Exception:
                continue
        return 0

    def _tab_hwnd_of_window(self, w):
        """取某个 Shell 窗口对象（IWebBrowser2）所属【标签】的控件窗口句柄。

        关键：w.HWND 返回的是主窗口句柄（多标签下三个候选全一样，无法区分）；
        而 IShellBrowser::GetWindow() 返回的才是该标签自己的 ShellTabWindowClass 句柄。
        pywin32 原生支持 IServiceProvider::QueryService，可直接取到 IShellBrowser。
        """
        import pythoncom
        try:
            from win32com.shell import shell as _shell
            iid_sb = _shell.IID_IShellBrowser
        except Exception:
            iid_sb = pythoncom.MakeIID("{000214E2-0000-0000-C000-000000000046}")
        try:
            sp = w._oleobj_.QueryInterface(pythoncom.IID_IServiceProvider)
            sb = sp.QueryService(iid_sb, iid_sb)
            return int(sb.GetWindow())
        except Exception as e:
            self._sb_err = f"{e}"
            return 0

    def _resolve_active_view(self, fg_hwnd, prefer_selected: bool = False):
        """解析前台资源管理器「当前活动标签」的文件夹视图。

        返回 (doc, n_tabs)：
          doc 非 None            → 拿到活动视图，可用 .SelectedItems() / .Folder.Self.Path
          doc 为 None 且 n_tabs==0 → 前台窗口不是资源管理器
          doc 为 None 且 n_tabs>0  → 是资源管理器，但多标签下无法确定活动标签
        """
        self._sb_err = ""
        shell = win32com.client.Dispatch("Shell.Application")
        candidates = []
        for w in shell.Windows():
            try:
                if int(w.HWND) == fg_hwnd:
                    candidates.append(w)
            except Exception:
                continue

        if not candidates:
            return None, 0
        if len(candidates) == 1:
            try:
                return candidates[0].Document, 1
            except Exception:
                return None, 1

        # ── 1. 主路径：z-order 最顶的 ShellTabWindowClass = 活动标签，
        #        再用 IShellBrowser::GetWindow() 找出属于它的那个 Shell 窗口对象 ──
        active_tab = self._active_tab_hwnd(fg_hwnd)
        self._last_tab_diag = f"活动标签 hwnd={active_tab}，候选 {len(candidates)} 个"
        if active_tab:
            seen = []
            for w in candidates:
                tab = self._tab_hwnd_of_window(w)
                seen.append(tab)
                if tab and tab == active_tab:
                    try:
                        return w.Document, len(candidates)
                    except Exception:
                        pass
            self._last_tab_diag += f"，各标签 hwnd={seen}"
            if self._sb_err:
                self._last_tab_diag += f"，IShellBrowser 错误({self._sb_err})"

        # ── 2. 回退：窗口标题与各标签目录比对 ──
        try:
            title = (win32gui.GetWindowText(fg_hwnd) or "").strip()
        except Exception:
            title = ""
        if title:
            def _leaf(s: str) -> str:
                return re.split(r'[\\/]', s.rstrip("\\/"))[-1].lower()
            t_full, t_leaf = title.rstrip("\\/").lower(), _leaf(title)
            for w in candidates:
                try:
                    doc_w = w.Document
                except Exception:
                    continue
                p = self._view_path(doc_w)
                if p and (p.rstrip("\\/").lower() == t_full or _leaf(p) == t_leaf):
                    return doc_w, len(candidates)

        # ── 3. 回退：选用「确实有选中项」的标签（仅需要选中项的功能可用）──
        if prefer_selected:
            for w in candidates:
                try:
                    doc_w = w.Document
                    if doc_w.SelectedItems().Count > 0:
                        return doc_w, len(candidates)
                except Exception:
                    continue

        return None, len(candidates)

    def mkdir_fast_create(self):
        """顺序新建热键触发：在当前资源管理器选中文件夹旁边新建下一个编号文件夹。"""
        if not self.chk_mkdir.isChecked():
            self.list_widget.addItem("⚠️ 请先启用速建文件夹")
            return
        if not _WIN_OK:
            self.list_widget.addItem("❌ 功能不可用：缺少 pywin32")
            return
        try:
            fg_hwnd = win32gui.GetForegroundWindow()
            if not fg_hwnd:
                self.list_widget.addItem("⚠️ 无法获取前台窗口，请先点击资源管理器")
                return

            doc, n_tabs = self._resolve_active_view(fg_hwnd, prefer_selected=True)

            if doc is None and n_tabs == 0:
                self.list_widget.addItem("⚠️ 前台窗口不是资源管理器")
                return
            if doc is None:
                self.list_widget.addItem(
                    f"⚠️ 未选中任何文件夹，请先选中一个文件夹再按 {self._current_mkdir_key()}"
                    f"（检测到 {n_tabs} 个标签）"
                )
                return

            try:
                sel = doc.SelectedItems()
                count = sel.Count
            except Exception:
                self.list_widget.addItem("❌ 无法读取选中项")
                return

            if count == 0:
                self.list_widget.addItem(
                    f"⚠️ 未选中任何文件夹，请先选中一个文件夹再按 {self._current_mkdir_key()}"
                )
                return
            if count > 1:
                self.list_widget.addItem(f"⚠️ 选中了 {count} 个项目，每次只能对 1 个文件夹操作")
                return

            item = sel.Item(0)
            target_path = item.Path

            if not os.path.isdir(target_path):
                self.list_widget.addItem(f"⚠️ 选中的是文件，请选中一个文件夹再按 {self._current_mkdir_key()}")
                return

            parent_dir  = os.path.dirname(target_path)
            folder_name = os.path.basename(target_path)
            # 目录感知：扫描同级同模式文件夹取最大编号 +1，跳过已存在的编号
            new_name    = self._next_available_folder_name(parent_dir, folder_name)
            new_path    = os.path.join(parent_dir, new_name)

            os.makedirs(new_path)
            self._push_record(
                f"✅ 速建文件夹成功：{new_name}",
                select=True,
                filepath=new_path,
            )

        except Exception as e:
            log.exception("速建文件夹失败")
            self.list_widget.addItem(f"❌ 速建文件夹错误：{e}")

    def mkdir_direct_create_a(self):
        """F9 触发：新建名称 A（默认 Grok）的文件夹。"""
        self._mkdir_direct_create(self.mkdir_name, "Grok", "F9")

    def mkdir_direct_create_b(self):
        """F10 触发：新建名称 B（默认 Qwen）的文件夹。"""
        self._mkdir_direct_create(self.mkdir_name_b, "Qwen", "F10")

    def _mkdir_direct_create(self, name_edit, default_name: str, key_label: str):
        """在当前资源管理器活动标签内，仅当未选中任何项时，
        新建一个以 name_edit 内容命名的空文件夹。"""
        if not self.chk_mkdir.isChecked():
            self.list_widget.addItem("⚠️ 请先启用速建文件夹")
            return
        if not _WIN_OK:
            self.list_widget.addItem("❌ 功能不可用：缺少 pywin32")
            return
        name = name_edit.text().strip() or default_name
        # 清洗非法字符；若清洗后只剩下划线/空白（如输入了 "///"），回退默认名
        name = re.sub(r'[\\/:*?"<>|]+', "_", name).strip()
        if not name.strip("_ "):
            name = default_name
        try:
            fg_hwnd = win32gui.GetForegroundWindow()
            if not fg_hwnd:
                self.list_widget.addItem("⚠️ 无法获取前台窗口，请先点击资源管理器")
                return

            doc, n_tabs = self._resolve_active_view(fg_hwnd, prefer_selected=False)

            if doc is None and n_tabs == 0:
                self.list_widget.addItem("⚠️ 前台窗口不是资源管理器")
                return
            if doc is None:
                # 认不出当前活动标签：宁可不建，也不要建到错误的目录里
                diag = getattr(self, "_last_tab_diag", "无诊断")
                self.list_widget.addItem("⚠️ 无法确定当前标签，已取消新建")
                self.list_widget.addItem(f"🔍 诊断：{diag}；Shell.Windows 候选 {n_tabs} 个")
                self.list_widget.scrollToBottom()
                self._log_problem(f"🔍 {key_label} 定位失败：{diag}；候选 {n_tabs} 个")
                return

            # 仅在“未选中任何项”时才直接新建
            try:
                count = doc.SelectedItems().Count
            except Exception:
                count = 0
            if count > 0:
                self.list_widget.addItem(
                    f"ℹ️ 已选中 {count} 项，{key_label} 直接新建仅在未选中任何项时生效"
                )
                return

            # 取当前活动标签所在目录
            cur_dir = self._view_path(doc)
            if not cur_dir or not os.path.isdir(cur_dir):
                self.list_widget.addItem(
                    f"⚠️ 无法确定当前文件夹｜取到的路径={cur_dir!r}｜doc={type(doc).__name__}"
                )
                self.list_widget.scrollToBottom()
                return

            new_path = self._unique_path(os.path.join(cur_dir, name))
            os.makedirs(new_path)
            self._push_record(
                f"✅ 直接新建成功：{os.path.basename(new_path)}",
                select=True,
                filepath=new_path,
            )

        except Exception as e:
            log.exception("直接新建文件夹失败")
            self.list_widget.addItem(f"❌ 直接新建错误：{e}")

    # ────────────────────────────────────────
    # F6 记录模式
    # ────────────────────────────────────────
    # ────────────────────────────────────────
    # F6 记录模式
    # ────────────────────────────────────────
    def _refresh_icon(self) -> QIcon:
        """20×20 圆形双箭头刷新图标。"""
        s = 20.0
        pm = QPixmap(int(s), int(s))
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        col = QColor(tk("text_mut"))
        pen = QPen(col, 2.0)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        cx, cy = s / 2, s / 2
        r = s * 0.32
        # 两个半弧拼成环形箭头
        p.drawArc(QRectF(cx - r, cy - r, r * 2, r * 2), 30 * 16, 150 * 16)
        p.drawArc(QRectF(cx - r, cy - r, r * 2, r * 2), 210 * 16, 150 * 16)
        # 上箭头
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(col))
        path1 = QPainterPath()
        x1, y1 = cx + r * 0.45, cy - r
        path1.moveTo(x1, y1)
        path1.lineTo(x1 - s * 0.1, y1 - s * 0.12)
        path1.lineTo(x1 + s * 0.1, y1 - s * 0.12)
        path1.closeSubpath()
        p.drawPath(path1)
        # 下箭头
        path2 = QPainterPath()
        x2, y2 = cx - r * 0.45, cy + r
        path2.moveTo(x2, y2)
        path2.lineTo(x2 + s * 0.1, y2 + s * 0.12)
        path2.lineTo(x2 - s * 0.1, y2 + s * 0.12)
        path2.closeSubpath()
        p.drawPath(path2)
        p.end()
        return QIcon(pm)

    def _restyle_record_btn(self, state="idle"):
        """state: idle / recording / frozen（批处理中灰化锁定）"""
        # 冻结期间其它路径的 restyle 不得盖掉冻结外观
        if getattr(self, "_record_btn_frozen", False):
            state = "frozen"
        if state == "recording":
            bg, fg, bd = "#3B82F6", "#ffffff", "#2563EB"
            hover = bd
        elif state == "frozen":
            # 与侧栏自动下载开关冻结态一致：slate 灰蓝、无 hover 反馈
            bg, fg, bd = "#64748B", "#E2E8F0", "#475569"
            hover = bg
        else:
            bg = tk("input_bg")
            fg = tk("text")
            bd = tk("border")
            hover = bd
        self.btn_record.setStyleSheet(
            f"QPushButton{{background:{bg};color:{fg};"
            f"border:1px solid {bd};border-radius:6px;"
            f"padding:6px 10px;font-size:12px;font-weight:bold;}}"
            f"QPushButton:hover{{background:{hover};}}"
            f"QPushButton:disabled{{background:{bg};color:{fg};border:1px solid {bd};}}"
        )

    _RECORD_UI_FROZEN_TIP = "批处理进行中 · 暂不可用（中止请按 F6）"

    def set_record_btn_frozen(self, frozen: bool):
        """批处理进行中：冻结「开始记录F6」与记录卡（禁止点进记录/再开批处理；F6 热键仍可中止）。"""
        frozen = bool(frozen)
        btn_was = bool(getattr(self, "_record_btn_frozen", False))
        cards_was = bool(getattr(self, "_record_cards_frozen", False))
        if btn_was == frozen and cards_was == frozen:
            return
        self._record_btn_frozen = frozen
        self._record_cards_frozen = frozen

        btn = getattr(self, "btn_record", None)
        if btn is not None:
            if frozen:
                btn.setEnabled(False)
                btn.setCursor(Qt.ForbiddenCursor)
                btn.setToolTip("批处理进行中 · 记录模式暂不可用（中止请按 F6）")
                self._restyle_record_btn("frozen")
            else:
                btn.setEnabled(True)
                btn.setCursor(Qt.PointingHandCursor)
                btn.setToolTip("")
                self._restyle_record_btn(
                    "recording" if getattr(self, "_recording", False) else "idle"
                )

        # 记录卡：就地改样式/可点，或解冻时整表刷新恢复正常外观
        if hasattr(self, "_record_grid_layout"):
            if frozen:
                self._apply_record_cards_frozen_style()
            else:
                try:
                    self._refresh_record_cards()
                except Exception:
                    self._apply_record_cards_frozen_style()

    def _apply_record_cards_frozen_style(self):
        """按当前 _record_cards_frozen 刷新网格内全部记录卡外观与可点状态。"""
        layout = getattr(self, "_record_grid_layout", None)
        if layout is None:
            return
        frozen = bool(getattr(self, "_record_cards_frozen", False))
        tip = self._RECORD_UI_FROZEN_TIP if frozen else ""
        if frozen:
            bg, fg, bd = "#64748B", "#E2E8F0", "#475569"
            hover = bg
            cursor = Qt.ForbiddenCursor
        else:
            bg = tk("input_bg")
            fg = tk("text")
            bd = tk("border")
            hover = tk("sel_bg")
            cursor = Qt.PointingHandCursor
        for i in range(layout.count()):
            item = layout.itemAt(i)
            w = item.widget() if item is not None else None
            if w is None:
                continue
            try:
                if isinstance(w, _RecordCardChip):
                    w.apply_chrome(bg, fg, bd, hover)
                w.setEnabled(not frozen)
                w.setCursor(cursor)
                w.setToolTip(tip)
            except Exception:
                pass

    def _btn_record_clicked(self):
        if getattr(self, "_record_btn_frozen", False):
            return
        self._toggle_record_mode()

    # ── 随机记录名池（修仙/玄幻风，2~3 汉字）────
    _RECORD_NAMES = [
        "废灵根", "聚灵丹", "聚宝盆", "炼妖壶", "斩仙剑", "定海珠", "打神鞭",
        "封神榜", "长生诀", "九转丹", "破阵子", "乾坤袋", "缚龙索", "飞天梭",
        "迷魂幡", "落魄钟", "诛仙阵", "万剑诀", "天罡气", "地煞火", "化神丹",
        "筑基液", "金丹砂", "元婴果", "渡劫期", "炼虚丹", "合体果", "大乘丹",
        "飞升台", "百草园", "千机阁", "万象塔", "招魂幡", "避水珠", "风火轮",
        "阴阳镜", "捆仙绳", "番天印", "落宝钱", "五色旗", "混元伞", "紫金铃",
        "金刚琢", "太乙甲", "北斗盘", "南明离", "玄黄鼎", "造化炉", "开天斧",
        "残阳", "玄铁", "青莲", "龙鳞", "凤羽", "玉骨", "星砂", "月魄",
        "雷音", "冰心", "金翅", "紫气", "冥河", "凌云", "沧海", "幽兰",
        "苍穹", "碧落", "归墟", "九天", "太古", "玄黄", "鸿蒙", "万劫",
        "轮回", "无量", "混元", "两仪", "四象", "八卦", "星河", "流光",
    ]

    def _toggle_record_mode(self):
        # 批处理进行中：F6 = 预约「当前条处理完再停」，不进入记录模式
        if self._try_stop_batch_via_f6():
            return
        # 按钮冻结时不启停记录（热键中止批处理已在上方处理）
        if getattr(self, "_record_btn_frozen", False):
            return
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _try_stop_batch_via_f6(self) -> bool:
        """若有批处理在跑，转调 request_batch_stop；成功受理返回 True。"""
        try:
            w = self
            mw = None
            while w is not None:
                if getattr(w, "page_douyin", None) is not None or getattr(w, "page_gallery", None) is not None:
                    mw = w
                    break
                try:
                    w = w.parent()
                except Exception:
                    break
            if mw is None:
                return False
            # 自动下载引擎（PageVideo，速存/图集批处理统一走此）
            pv = getattr(mw, "page_douyin", None)
            if pv is not None and getattr(pv, "_batch_mode", False):
                if hasattr(pv, "request_batch_stop"):
                    return bool(pv.request_batch_stop())
                return False
            # 图集：本地批处理 或 序列补全
            pg = getattr(mw, "page_gallery", None)
            if pg is not None:
                for attr in ("page_eh", "page_pixiv", "page_hitomi"):
                    sub = getattr(pg, attr, None)
                    if sub is None:
                        continue
                    filling = bool(
                        getattr(sub, "_fill_active_once", False)
                        or getattr(sub, "_fill_only_names", None)
                    )
                    if getattr(sub, "_batch_mode", False) or filling:
                        if hasattr(sub, "request_batch_stop"):
                            return bool(sub.request_batch_stop())
                        return False
        except Exception:
            pass
        return False

    def _resolve_record_path(self) -> str:
        """返回记录文件路径：每次调用都新建一个文件。
        勾选「新建随机记录名」→ 随机中文名；不勾选 → 当天顺序编号。
        平铺在 data/ 根，文件带 card_ 前缀（如 card_记录文件_MMDD_NN.txt）。"""
        try:
            from utils.app_paths import cards_dir
            rec_dir = cards_dir()
        except Exception:
            rec_dir = os.path.normpath(
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
            )
            os.makedirs(rec_dir, exist_ok=True)
        md = time.strftime("%m%d")

        if not self.chk_new_record.isChecked():
            pat = re.compile(rf'^card_记录文件_{md}(?:_(\d+))?\.txt$')
            max_idx = 0
            try:
                for name in os.listdir(rec_dir):
                    m = pat.match(name)
                    if m:
                        n = int(m.group(1)) if m.group(1) else 0
                        if n > max_idx:
                            max_idx = n
            except Exception:
                pass
            next_idx = max_idx + 1
            if next_idx > 99:
                next_idx = 1
            return os.path.join(rec_dir, f"card_记录文件_{md}_{next_idx:02d}.txt")

        import random
        tried = set()
        while len(tried) < len(self._RECORD_NAMES):
            name = random.choice(self._RECORD_NAMES)
            if name in tried:
                continue
            tried.add(name)
            path = os.path.join(rec_dir, f"card_{name}.txt")
            if not os.path.exists(path):
                return path
        ts = int(time.time())
        return os.path.join(rec_dir, f"card_无名_{ts}.txt")

    def _init_record_file(self):
        """启动时刷新记录卡片网格。"""
        self._refresh_record_cards()

    def _record_card_clicked(self, filepath: str):
        """点击记录卡片弹出弹窗：预览内容 + 继续记录 / 批处理 / 删除。"""
        if getattr(self, "_record_cards_frozen", False):
            return
        if not filepath or not os.path.isfile(filepath):
            return
        fname = os.path.basename(filepath)
        name_no_ext = re.sub(r'\.txt$', '', fname)
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QTextEdit
        dlg = QDialog(self)
        dlg.setWindowTitle("记录文件")
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dlg.resize(640, 360)
        dlg.setMinimumSize(480, 280)
        dlg.setStyleSheet(self.styleSheet())
        v = QVBoxLayout(dlg)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(10)

        preview = QTextEdit()
        preview.setStyleSheet(
            f"QTextEdit{{background:{tk('input_bg')};color:{tk('text')};"
            f"border:1px solid {tk('border')};border-radius:6px;font-size:13px;}}"
        )
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            preview.setPlainText(content)
        except Exception:
            preview.setPlainText("（无法读取文件内容）")
        v.addWidget(preview, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        def _make_btn(text, bg=None, fg=None, bd=None, hover_bg=None):
            b = QPushButton(text)
            b.setFixedHeight(34)
            b.setCursor(Qt.PointingHandCursor)
            if bg:
                qss = (
                    f"QPushButton{{background:{bg};color:{fg or '#ffffff'};"
                    f"border:1px solid {bd or bg};border-radius:6px;font-size:13px;}}"
                )
                if hover_bg:
                    qss += f"QPushButton:hover{{background:{hover_bg};}}"
            else:
                qss = (
                    f"QPushButton{{background:{tk('input_bg')};color:{tk('text')};"
                    f"border:1px solid {tk('border')};border-radius:6px;font-size:13px;}}"
                )
                if hover_bg:
                    qss += f"QPushButton:hover{{background:{hover_bg};}}"
            b.setStyleSheet(qss)
            return b

        def _do_save_and_close():
            _do_save(filepath, preview.toPlainText())
            dlg.accept()

        def _do_save(path, text):
            try:
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(text)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
            except Exception as e:
                log.exception("运行记录保存失败 path=%s", path)
                from PyQt5.QtWidgets import QMessageBox
                message_box_warn(dlg, "保存失败", str(e))

        # 删除按钮：弹确认框
        btn_delete = _make_btn("删除", bg="#7f1d1d", fg="#fca5a5", bd="#991b1b", hover_bg="#991b1b")

        def _delete_with_confirm():
            from PyQt5.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                dlg, "确认删除", f"确定要删除 {name_no_ext} 吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self._delete_record_file(filepath, dlg)

        btn_delete.clicked.connect(_delete_with_confirm)
        btn_row.addWidget(btn_delete)

        btn_row.addStretch(1)

        # 右下三键配色顺序：黄 → 蓝 → 绿
        if not self._recording:
            btn_append = _make_btn(
                f"继续记录_{name_no_ext}",
                bg="#a16207", fg="#fef9c3", bd="#ca8a04", hover_bg="#ca8a04",
            )
            btn_append.clicked.connect(lambda: (self._start_recording_to(filepath), dlg.accept()))
            btn_row.addWidget(btn_append)

        btn_batch = _make_btn(
            f"批处理_{name_no_ext}",
            bg="#1d4ed8", fg="#dbeafe", bd="#2563eb", hover_bg="#2563eb",
        )
        btn_batch.clicked.connect(lambda: self._batch_from_record(filepath, dlg))
        btn_row.addWidget(btn_batch)

        btn_save = _make_btn(
            "保存并关闭",
            bg="#14532d", fg="#bbf7d0", bd="#166534", hover_bg="#166534",
        )
        btn_save.clicked.connect(_do_save_and_close)
        btn_row.addWidget(btn_save)

        v.addLayout(btn_row)

        # 恢复上次关闭时的窗口尺寸
        _saved_w, _saved_h = 0, 0
        try:
            from utils.user_prefs import load_user_prefs
            prefs, _ = load_user_prefs()
            fs = (prefs.get("fast_save") or {})
            dw = fs.get("record_dialog_w")
            dh = fs.get("record_dialog_h")
            if isinstance(dw, int) and isinstance(dh, int) and dw > 0 and dh > 0:
                _saved_w, _saved_h = dw, dh
        except Exception:
            pass

        # 事件过滤器：对话框隐藏时记下尺寸
        class _SizeSaver(QObject):
            def eventFilter(self, obj, event):
                if event.type() == QEvent.Hide:
                    try:
                        from utils.user_prefs import load_user_prefs, save_user_prefs
                        prefs, _ = load_user_prefs()
                        fs = prefs.get("fast_save")
                        if not isinstance(fs, dict):
                            fs = {}
                            prefs["fast_save"] = fs
                        fs["record_dialog_w"] = obj.width()
                        fs["record_dialog_h"] = obj.height()
                        save_user_prefs(prefs)
                        # 同步到主窗口内存
                        w2 = dlg.parent()
                        while w2:
                            if hasattr(w2, "_user_prefs") and isinstance(getattr(w2, "_user_prefs", None), dict):
                                w2._user_prefs = prefs
                                break
                            w2 = w2.parent()
                    except Exception:
                        pass
                return False

        _saver = _SizeSaver(dlg)
        dlg.installEventFilter(_saver)

        # 应用保存的尺寸（show 之后 resize 才能真正生效）
        if _saved_w > 0:
            QTimer.singleShot(10, lambda: dlg.resize(_saved_w, _saved_h))

        dlg.exec_()

        self._refresh_record_cards()

    def _batch_from_record(self, filepath: str, dlg=None):
        """从记录文件启动自动下载（混链统一走 PageVideo.start_batch_from_file）。"""
        if dlg is not None:
            try:
                dlg.accept()
            except Exception:
                pass
        pv = None
        mw = None
        w = self
        while w:
            if hasattr(w, "page_douyin"):
                pv = getattr(w, "page_douyin", None)
                mw = w
            w = w.parent()
        if pv is None or not hasattr(pv, "start_batch_from_file"):
            from PyQt5.QtWidgets import QMessageBox
            message_box_info(self, "提示", "未找到自动下载引擎，请确认程序已完整启动")
            return
        # 先切到视频页，便于日志可见；混链后续会再切到图集等
        if mw and hasattr(mw, "stack") and hasattr(mw, "btn_douyin"):
            try:
                idx = mw.stack.indexOf(pv)
                if idx >= 0:
                    mw._switch(idx, mw.btn_douyin)
            except Exception:
                pass
        if not pv.start_batch_from_file(filepath):
            try:
                self.list_widget.addItem("⚠ 自动下载未能启动（文件空/不存在/已有任务在跑）")
                self.list_widget.scrollToBottom()
            except Exception:
                pass

    @staticmethod
    def _normalize_record_line(text: str) -> str:
        """记录行规范化：去首尾空白、内部空白压成单空格（与写入时一致）。"""
        return " ".join(((text or "").strip()).split())

    def _load_record_seen(self, filepath: str) -> set:
        """从记录文件载入全部非空行（规范化），供全文件防重复。"""
        seen = set()
        path = (filepath or "").strip()
        if not path or not os.path.isfile(path):
            return seen
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                for raw in f:
                    line = self._normalize_record_line(raw)
                    if line and not line.startswith("#"):
                        seen.add(line)
        except Exception:
            pass
        return seen

    def _start_recording_to(self, filepath: str):
        """使用已有文件进入记录模式（追加写入）。F6 新建与「继续记录」共用。"""
        if self._recording:
            self._stop_recording()
        rec_dir = os.path.dirname(filepath)
        os.makedirs(rec_dir, exist_ok=True)
        self._record_file = filepath
        self._record_count = 0
        self._record_base_count = 0
        self._recording = True
        self.btn_record.setText("停止记录F6")
        self._restyle_record_btn("recording")
        # 全文件防重复：启动时载入文件内全部已有行（新建空文件则为空集）
        self._record_seen = self._load_record_seen(self._record_file)
        self._record_base_count = len(self._record_seen)
        existing = f"已有{self._record_base_count}行，" if self._record_base_count else ""
        self.lbl_record_hint.setText(f"内容将逐行写入{existing}，已加 0 条")
        tip = f"🔴 记录中 → {os.path.basename(self._record_file)}"
        if self._record_seen:
            tip += f"（已载入 {len(self._record_seen)} 条防重复）"
        self.list_widget.addItem(tip)
        self.list_widget.scrollToBottom()
        start_record_bubble(self._record_base_count)
        # 记录模式期间静默关掉速存三项，避免再刷三行「已关闭」
        self.set_all_features(False, quiet=True)
        self.chk_enable_all.blockSignals(True)
        self.chk_enable_all.setChecked(False)
        self.chk_enable_all.blockSignals(False)
        self.record_mode_toggled_sig.emit("started")
        self.request_show.emit()

    def _delete_record_file(self, filepath: str, dlg=None):
        """确认并删除记录文件。"""
        fname = os.path.basename(filepath)
        from PyQt5.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "确认删除", f"确定要删除 {fname} 吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            try:
                os.remove(filepath)
                self._refresh_record_cards()
                self.list_widget.addItem(f"已删除 {fname}")
                self.list_widget.scrollToBottom()
            except Exception as e:
                self.list_widget.addItem(f"删除失败：{e}")
                self.list_widget.scrollToBottom()
        if dlg:
            dlg.accept()

    @staticmethod
    def _is_record_card_file(fname: str, rec_dir: str) -> bool:
        """平铺后 data/ 根是否应显示为记录卡：只认 card_*.txt。

        data/ 根里还有 user.txt、points_calc.txt 等无关 .txt，必须靠
        card_ 前缀过滤，不能像以前那样“目录里的任意 .txt 都算”。
        排除：隐藏文件、非普通文件、保存中的 .tmp 残留命名。
        """
        if not fname or not isinstance(fname, str):
            return False
        if fname.startswith("."):
            return False
        # 原子保存产生 path + ".tmp"（xxx.txt.tmp），不是合法记录
        if fname.lower().endswith(".tmp"):
            return False
        if not fname.lower().endswith(".txt"):
            return False
        if not fname.startswith("card_"):
            return False
        # 去掉扩展名后仍须有名字（拒绝 ".txt"）
        if not fname[:-4].strip():
            return False
        try:
            return os.path.isfile(os.path.join(rec_dir, fname))
        except Exception:
            return False

    def _refresh_record_cards(self):
        """扫描 data/ 根（card_*.txt），为每个记录文件生成方形卡片。

        兼容程序生成名（card_记录文件_MMDD_xx / card_随机中文名）。
        """
        # 清空旧卡片
        layout = self._record_grid_layout
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        try:
            from utils.app_paths import cards_dir
            rec_dir = cards_dir()
        except Exception:
            rec_dir = os.path.normpath(
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
            )
        try:
            files = sorted(
                (f for f in os.listdir(rec_dir)
                 if self._is_record_card_file(f, rec_dir)),
                key=lambda f: os.path.getctime(os.path.join(rec_dir, f)),
            )
        except Exception:
            return

        frozen = bool(getattr(self, "_record_cards_frozen", False))
        if frozen:
            card_color, text_color, border_color = "#64748B", "#E2E8F0", "#475569"
            hover_color = card_color
            tip = self._RECORD_UI_FROZEN_TIP
        else:
            card_color = tk("input_bg")
            border_color = tk("border")
            text_color = tk("text")
            hover_color = tk("sel_bg")
            tip = ""

        for idx, fname in enumerate(files, 1):
            plain = fname[len("card_"):] if fname.startswith("card_") else fname
            m = re.match(r'记录文件_(\d{4})(?:_(\d+))?\.txt$', plain)
            if m:
                short_date = m.group(1)
                sub = m.group(2) if m.group(2) else ""
                seq = f"{idx:02d}" if not sub else sub.zfill(2)
                name = f"{short_date}-{seq}"
            else:
                name = re.sub(r'\.txt$', '', plain)

            fpath = os.path.join(rec_dir, fname)
            # 与记录模式防重复一致：非空、非 # 注释行计为一条
            count = len(self._load_record_seen(fpath))
            display = f"{name} {count}"

            card = _RecordCardChip(display)
            card.apply_chrome(card_color, text_color, border_color, hover_color)
            if frozen:
                card.setEnabled(False)
                card.setCursor(Qt.ForbiddenCursor)
                card.setToolTip(tip)
            else:
                card.setToolTip("")
                card.clicked.connect(lambda checked, p=fpath: self._record_card_clicked(p))
            layout.addWidget(card)

    def _start_recording(self):
        self._start_recording_to(self._resolve_record_path())

    def _stop_recording(self):
        self._recording = False
        cnt = self._record_count
        self._record_seen = set()

        self.btn_record.setText("开始记录F6")
        self._restyle_record_btn("idle")
        self._refresh_record_cards()

        self.lbl_record_hint.setText(f"上次记录了 {cnt} 条")

        self.list_widget.addItem(f"⏹️ 记录模式已停止 → 新增 {cnt} 条，"
                                  f"文件：{os.path.basename(self._record_file)}")
        self.list_widget.scrollToBottom()
        stop_record_bubble()

        # 恢复速存三项时静默，避免再刷三行「已启用」（停止提示已足够）
        self.set_all_features(True, quiet=True)
        self.chk_enable_all.blockSignals(True)
        self.chk_enable_all.setChecked(True)
        self.chk_enable_all.blockSignals(False)
        self.record_mode_toggled_sig.emit("stopped")
        self.request_show.emit()

    def _on_record_clipboard(self):
        if not self._recording:
            return
        if self._record_clip_ignore:
            return
        if not self._record_file:
            return

        self._record_clip_ignore = True
        try:
            cb = QApplication.clipboard()
            if cb and cb.mimeData().hasUrls():
                return
            text = (cb.text() or "").strip() if cb else ""
            if not text:
                return
            # 多行分享文案压成单行，避免批处理按行拆碎后平台识别失败
            text = self._normalize_record_line(text)
            if not text:
                return

            # 全文件防重复：与文件内（及本会话已写入）任意一行相同则忽略
            if text in self._record_seen:
                self.list_widget.addItem("⏭️ 已忽略重复记录：文件中已有相同内容")
                self.list_widget.scrollToBottom()
                try:
                    cb.clear()
                except Exception:
                    pass
                return

            with open(self._record_file, "a", encoding="utf-8") as fp:
                fp.write(text + "\n")

            self._record_seen.add(text)
            self._record_count += 1
            self.lbl_record_hint.setText(f"内容将逐行写入，已加 {self._record_count} 条")
            update_record_bubble_count(self._record_base_count + self._record_count)
            cb.clear()
        except Exception as e:
            self.list_widget.addItem(f"❌ 记录失败：{e}")
            self.list_widget.scrollToBottom()
        finally:
            self._record_clip_ignore = False

    def closeEvent(self, e):
        self.stop_background_checks()
        self._unregister_txt_hotkey()
        self._unregister_mkdir_hotkey()
        super().closeEvent(e)
    def stop_background_checks(self):
        """主窗口真正关闭时调用（见 ui_main.py MainWindow.closeEvent）。
        本页面是嵌在 QStackedWidget 里的子页面，Qt 不会对它单独触发 closeEvent，
        所以之前那个 closeEvent 里的清理其实从没被执行过——这才是报错的根本原因。
        这里先置位 _closing 挡住新的检测线程，再停掉定时器与 WS 服务；已经在跑的检测线程
        自己也会在 _do_check / _set_plugin_ui 里检查这个标志位，安全退出。"""
        if self._closing:
            return
        self._closing = True
        try:
            stop_record_bubble()
        except Exception:
            pass
        try:
            self._nm_check_timer.stop()
        except Exception:
            pass
        try:
            self._imgonly_timer.stop()
        except Exception:
            pass
        try:
            self._unregister_txt_hotkey()
        except Exception:
            pass
        try:
            self._unregister_mkdir_hotkey()
        except Exception:
            pass
        try:
            self._unregister_recording_hotkey()
        except Exception:
            pass
        try:
            self._parsing_items.clear()
        except Exception:
            pass
        import pages.page_fast_save as _pfs
        _pfs._ws_save_started_cb = None
        _pfs._ws_save_done_cb = None
        try:
            stop_ws_server()
        except Exception:
            _ws_log.exception("停止 WS 服务失败")

    def shutdown(self):
        """与 stop_background_checks 同义，供主窗口统一收口。"""
        self.stop_background_checks()

    def _bind_container_switch(self):
        w = self.parent()
        while w:
            if isinstance(w, (QTabWidget, QStackedWidget)):
                self._container = w
                try: w.currentChanged.connect(self._on_container_current_changed)
                except Exception: pass
                break
            w = w.parent()

    def _on_container_current_changed(self, idx: int):
        """切到此页面时自动刷新记录卡片。"""
        try:
            w = self._container.widget(idx) if self._container else None
            if w is self:
                self._refresh_record_cards()
        except Exception:
            pass

    # ── 序列文件检查 ─────────────────────────────────────────────────────────────
    def on_sequence_fill_done(
        self,
        *,
        ok: bool,
        summary: str = "",
        folder: str = "",
        detail: str = "",
        still_n: int = 0,
        planned_n: int = 0,
    ):
        """图集页补全结束后回调：刷新序列检查文案，给用户明确结果。"""
        folder = (folder or "").strip() or getattr(self, "_check_folder", "") or ""
        if folder and os.path.isdir(folder):
            self._check_folder = folder
            try:
                self._check_scan()
            except Exception:
                pass
        head = (summary or "").strip() or ("补全成功" if ok else "补全失败")
        color = "#22c55e" if ok else "#ef4444"
        extra = f"<br><b style='color:{color};'>{head}</b>"
        if still_n > 0:
            extra += f"<br><span style='color:#f59e0b;'>仍缺 {still_n}"
            if planned_n:
                extra += f"/{planned_n}"
            extra += " 项</span>"
        elif detail:
            d = (detail or "").strip().split("\n")[0][:80]
            if d:
                extra += f"<br><span style='color:#94a3b8;'>{d}</span>"
        try:
            cur = self.lbl_check_result.text() or ""
            # 去掉旧的补全结果行，避免叠加
            self.lbl_check_result.setText(cur + extra)
            self.lbl_check_result.setTextFormat(Qt.RichText)
        except Exception:
            pass
        # 进度气泡已由 e-hentai 补全流程关闭；此处不再弹独立小气泡，避免叠两层

    def _check_browse_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择要检查的文件夹")
        if path:
            self._check_folder = path
            result = self._check_scan()
            # 勾选「补全」且确有缺失 → 先弹 3 秒分析结果，再转入图集下载
            if (
                result
                and getattr(self, "chk_check_fill", None) is not None
                and self.chk_check_fill.isChecked()
                and int(result.get("total_missing") or 0) > 0
            ):
                if self._check_show_analysis_dialog(result):
                    self._check_start_fill(result)

    def _check_show_analysis_dialog(self, scan_result: dict) -> bool:
        """指定文件夹后的分析结果弹窗（约 3 秒），说明下一步将去 e-hentai 补全。

        返回 True 继续补全；False 用户取消。
        """
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QDialogButtonBox

        folder = (scan_result or {}).get("folder") or ""
        total_missing = int((scan_result or {}).get("total_missing") or 0)
        missing_names = list((scan_result or {}).get("missing_names") or [])
        has_report = bool((scan_result or {}).get("has_report"))
        groups_n = int((scan_result or {}).get("groups_n") or 0)
        range_hint = (scan_result or {}).get("ranges_text") or ""
        expected_total = int((scan_result or {}).get("expected_total") or 0)
        leaf = os.path.basename(folder.rstrip("\\/")) if folder else "（未选）"

        preview = "、".join(missing_names[:8])
        if len(missing_names) > 8:
            preview += f" 等 {len(missing_names)} 个"
        if not preview:
            preview = "（无）"

        dlg = QDialog(self)
        dlg.setWindowTitle("序列检查 · 分析结果")
        dlg.setModal(True)
        dlg.setMinimumWidth(440)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(10)

        title = QLabel("分析完成")
        title.setStyleSheet("font-size:15px; font-weight:700; background:transparent;")
        lay.addWidget(title)

        extra_lines = ""
        if groups_n:
            extra_lines += f"<br><b>序列</b>　{groups_n} 组"
        if range_hint:
            extra_lines += f"<br><b>已有范围</b>　{range_hint}"
        if expected_total > 0:
            extra_lines += f"<br><b>应下总数</b>　{expected_total}（来自清单）"

        body = QLabel(
            f"<b>目录</b>　{leaf}"
            + extra_lines
            + f"<br><b>缺失</b>　<span style='color:#ef4444;font-weight:700;'>{total_missing}</span> 个"
            + f"<br><b>示例</b>　{preview}<br>"
            f"<b>清单</b>　"
            + ("已找到 _缺失文件清单.txt" if has_report else "未找到清单（稍后可粘贴图库链接）")
            + "<br><br>"
            "下一步将转到 <b>图集下载 · e-hentai.org</b>，"
            "定点解析并只下载上述缺失序号。"
        )
        body.setWordWrap(True)
        body.setTextFormat(Qt.RichText)
        body.setStyleSheet("background:transparent; font-size:13px; line-height:1.4;")
        lay.addWidget(body)

        countdown_sec = 3
        lbl_cd = QLabel(f"{countdown_sec} 秒后自动开始补全…")
        lbl_cd.setStyleSheet("background:transparent; color:#94a3b8; font-size:12px;")
        lay.addWidget(lbl_cd)

        btn_box = QDialogButtonBox()
        btn_go = btn_box.addButton("立即开始", QDialogButtonBox.AcceptRole)
        btn_cancel = btn_box.addButton("取消", QDialogButtonBox.RejectRole)
        btn_go.setDefault(True)
        lay.addWidget(btn_box)

        state = {"left": countdown_sec, "done": False}

        def _tick():
            if state["done"]:
                return
            state["left"] -= 1
            if state["left"] <= 0:
                state["done"] = True
                timer.stop()
                dlg.accept()
                return
            lbl_cd.setText(f"{state['left']} 秒后自动开始补全…")
            btn_go.setText(f"立即开始（{state['left']}）")

        timer = QTimer(dlg)
        timer.setInterval(1000)
        timer.timeout.connect(_tick)
        btn_go.setText(f"立即开始（{countdown_sec}）")

        def _accept():
            state["done"] = True
            timer.stop()
            dlg.accept()

        def _reject():
            state["done"] = True
            timer.stop()
            dlg.reject()

        btn_box.accepted.connect(_accept)
        btn_box.rejected.connect(_reject)
        timer.start()

        ok = dlg.exec_() == QDialog.Accepted
        timer.stop()
        return bool(ok)

    def _check_scan(self):
        """扫描编号序列缺失。返回 dict 或 None（失败/无序列）。"""
        folder = getattr(self, "_check_folder", "") or ""
        if not folder or not os.path.isdir(folder):
            self.lbl_check_result.setText("请先选择文件夹")
            return None

        try:
            all_files = os.listdir(folder)
        except Exception as e:
            self.lbl_check_result.setText(f"读取失败: {e}")
            return None

        files = [f for f in all_files if os.path.isfile(os.path.join(folder, f))]

        if not files:
            self.lbl_check_result.setText("文件夹内无文件")
            return None

        ignore_ext = self.chk_ignore_ext.isChecked()
        groups = {}
        unnumbered = []
        for f in files:
            name, ext = os.path.splitext(f)
            m = re.search(r'(\d+)$', name)
            if m:
                prefix = name[:m.start()]
                num = int(m.group(1))
                digits = m.group(1)
                key = (prefix.lower(), ext.lower()) if not ignore_ext else (prefix.lower(),)
                groups.setdefault(key, []).append((num, digits, f))
            else:
                unnumbered.append(f)

        if not groups:
            self.lbl_check_result.setText("未检测到编号序列")
            return None

        # 清单「应下：N」→ 把序列范围扩到 1..N（否则尾部整段缺失扫不出来）
        expected_total = 0
        try:
            from pages.page_gallery import find_missing_report, parse_missing_report
            _rp = find_missing_report(folder)
            if _rp:
                _info = parse_missing_report(_rp)
                expected_total = int(_info.get("expected_count") or 0)
        except Exception:
            expected_total = 0

        lines = []
        total_missing = 0
        all_missing_nums = []

        for key, items in groups.items():
            items.sort(key=lambda x: x[0])
            nums = [it[0] for it in items]
            num_min, num_max = nums[0], nums[-1]
            zfill = max(len(it[1]) for it in items)
            if ignore_ext:
                prefix = key[0]
            else:
                prefix, _ext = key
                prefix = prefix or ""
            # EH 风格纯数字序号：有「应下」时从 1 扫到应下总数
            is_pure_num = (not prefix) or (str(prefix).strip() == "")
            if expected_total > 0 and is_pure_num:
                num_min = 1
                num_max = max(num_max, expected_total)
                zfill = max(zfill, len(str(num_max)), 4)
            missing = sorted(set(range(num_min, num_max + 1)) - set(nums))
            total_missing += len(missing)
            for n in missing:
                all_missing_nums.append(f"{prefix}{str(n).zfill(zfill)}")

        # Line 1：X 组序列
        lines.append(f"{len(groups)} 组序列")

        # Line 2：范围
        ranges = []
        for key, items in groups.items():
            items.sort(key=lambda x: x[0])
            nums = [it[0] for it in items]
            zfill = max(len(it[1]) for it in items)
            ranges.append(f"{str(nums[0]).zfill(zfill)}~{str(nums[-1]).zfill(zfill)}")
        lines.append("，".join(ranges))

        # Line 3：缺失
        if total_missing:
            missing_preview = "、".join(all_missing_nums[:5])
            if len(all_missing_nums) > 5:
                missing_preview += f" 等{len(all_missing_nums)}个"
            lines.append(f"<b style='color:#ef4444;'>缺失 {total_missing} 个：{missing_preview}</b>")
        else:
            lines.append("无缺失")

        # 补全提示：目录内是否有图集清单
        try:
            from pages.page_gallery import MISSING_REPORT_NAME, find_missing_report
            has_report = bool(find_missing_report(folder))
        except Exception:
            MISSING_REPORT_NAME = "_缺失文件清单.txt"
            has_report = os.path.isfile(os.path.join(folder, MISSING_REPORT_NAME))
        if getattr(self, "chk_check_fill", None) is not None and self.chk_check_fill.isChecked():
            if has_report:
                lines.append("<span style='color:#22c55e;'>已找到清单 · 可补全</span>")
            else:
                lines.append("<span style='color:#f59e0b;'>未找到清单</span>")

        self.lbl_check_result.setText("<br>".join(lines))
        self.lbl_check_result.setTextFormat(Qt.RichText)
        return {
            "folder": folder,
            "total_missing": total_missing,
            "missing_names": list(all_missing_nums),
            "has_report": has_report,
            "groups_n": len(groups),
            "ranges_text": "，".join(ranges) if ranges else "",
            "expected_total": int(expected_total or 0),
        }

    def _check_ask_report_or_url(self, start_dir: str = "") -> str:
        """找不到清单时：可填 清单路径 / 目录 / 或直接粘贴 e-hentai 图库链接。"""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QDialogButtonBox

        try:
            from pages.page_gallery import MISSING_REPORT_NAME
        except Exception:
            MISSING_REPORT_NAME = "_缺失文件清单.txt"

        dlg = QDialog(self)
        dlg.setWindowTitle("补全需要图库链接")
        dlg.setModal(True)
        dlg.setMinimumWidth(460)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(8)
        tip = QLabel(
            f"目录中没有「{MISSING_REPORT_NAME}」（或无法读出链接）。\n"
            "请任选其一：\n"
            "  · 粘贴 e-hentai 图库页链接（形如 https://e-hentai.org/g/…/…/）\n"
            "  · 或指定清单文件 / 原下载目录"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("background:transparent;")
        lay.addWidget(tip)

        row = QHBoxLayout()
        edit = QLineEdit()
        edit.setPlaceholderText("https://e-hentai.org/g/数字/token/  或  清单路径")
        # 不预填目录路径，避免一点确定又走「仍无清单」；优先让用户贴图库链接
        row.addWidget(edit, 1)

        def _browse():
            path, _ = QFileDialog.getOpenFileName(
                dlg,
                "选择缺失文件清单",
                edit.text().strip() or start_dir or "",
                "文本 (*.txt);;所有文件 (*.*)",
            )
            if path:
                edit.setText(path.replace("\\", "/"))
                return
            folder = QFileDialog.getExistingDirectory(
                dlg, "选择原下载目录", edit.text().strip() or start_dir or ""
            )
            if folder:
                edit.setText(folder.replace("\\", "/"))

        btn_browse = QPushButton("浏览…")
        btn_browse.setCursor(Qt.PointingHandCursor)
        btn_browse.clicked.connect(_browse)
        row.addWidget(btn_browse)
        lay.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)

        if dlg.exec_() != QDialog.Accepted:
            return ""
        return (edit.text() or "").strip().strip('"')

    def _check_start_fill(self, scan_result: dict):
        """序列真实缺失 + 图库源链接 → e-hentai 页解析并只补缺失。"""
        from PyQt5.QtWidgets import QMessageBox

        folder = (scan_result or {}).get("folder") or getattr(self, "_check_folder", "") or ""
        missing_names = list((scan_result or {}).get("missing_names") or [])
        if not folder or not missing_names:
            return

        try:
            from pages.page_gallery import (
                find_missing_report,
                parse_missing_report,
                extract_eh_gallery_url,
                MISSING_REPORT_NAME,
            )
        except Exception as e:
            message_box_warn(self, "补全失败", f"无法加载图集模块：{e}")
            return

        report_path = find_missing_report(folder)
        source_url = ""
        if report_path:
            info = parse_missing_report(report_path)
            source_url = extract_eh_gallery_url(info.get("source_url") or "") or (
                info.get("source_url") or ""
            ).strip()
            if not source_url and info.get("path"):
                # 再读全文抽一次
                try:
                    with open(info["path"], "r", encoding="utf-8", errors="ignore") as f:
                        source_url = extract_eh_gallery_url(f.read())
                except Exception:
                    pass

        if not source_url:
            user_in = self._check_ask_report_or_url(folder)
            if not user_in:
                try:
                    self.lbl_check_result.setText(
                        self.lbl_check_result.text()
                        + "<br><span style='color:#94a3b8;'>已取消补全</span>"
                    )
                except Exception:
                    pass
                return
            # 用户直接贴了图库链接
            source_url = extract_eh_gallery_url(user_in)
            if not source_url:
                # 当路径：找清单
                rp = find_missing_report(user_in)
                if not rp and os.path.isfile(user_in):
                    rp = os.path.abspath(user_in)
                if rp:
                    report_path = rp
                    info = parse_missing_report(rp)
                    source_url = extract_eh_gallery_url(
                        (info.get("source_url") or "") + "\n" + (user_in or "")
                    )
                    if not source_url:
                        try:
                            with open(rp, "r", encoding="utf-8", errors="ignore") as f:
                                source_url = extract_eh_gallery_url(f.read())
                        except Exception:
                            pass
            if not source_url:
                message_box_warn(
                    self,
                    "补全失败",
                    "未能得到 e-hentai 图库链接。\n"
                    "请粘贴形如：\nhttps://e-hentai.org/g/数字/token/\n"
                    f"或提供含该链接的「{MISSING_REPORT_NAME}」。",
                )
                return

        source_url = extract_eh_gallery_url(source_url) or source_url.strip()
        low = source_url.lower()
        if "e-hentai.org" not in low and "exhentai.org" not in low:
            message_box_info(
                self,
                "不支持补全",
                "序列补全仅支持 e-hentai.org（含 exhentai.org）。\n"
                f"当前链接：{source_url[:120]}",
            )
            return
        if "hath.network" in low:
            message_box_warn(
                self,
                "链接类型不对",
                "需要的是图库页链接（…/g/ID/token/），\n"
                "不是清单里 hath.network 的单张图片直链。",
            )
            return

        # 切到图集页并 handoff（固定 e-hentai.org 子页）
        mw = self.window()
        pg = getattr(mw, "page_gallery", None) if mw is not None else None
        if pg is None:
            message_box_warn(self, "补全失败", "未找到图集下载页")
            return
        try:
            if hasattr(mw, "_switch") and hasattr(mw, "btn_gallery"):
                mw._switch(mw.stack.indexOf(pg), mw.btn_gallery)
        except Exception:
            pass

        ok = False
        try:
            ok = bool(
                pg.handoff_fill_missing(
                    source_url,
                    folder,
                    missing_names,
                    report_path=report_path or "",
                )
            )
        except Exception as e:
            message_box_warn(self, "补全失败", str(e))
            return

        if ok:
            try:
                cur = self.lbl_check_result.text() or ""
                self.lbl_check_result.setText(
                    cur
                    + f"<br><span style='color:#22c55e;'>"
                    f"已转入 e-hentai · 补 {len(missing_names)} 项（见鼠标旁进度气泡）</span>"
                )
            except Exception:
                pass
        else:
            try:
                show_cursor_toast("序列补全", "未能启动", accent="warn")
            except Exception:
                pass
