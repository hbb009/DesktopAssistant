"""Download image URLs using a managed curl child process."""

from collections import deque
from email import policy
from email.parser import BytesHeaderParser
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import unquote, urlsplit
from urllib.request import getproxies, proxy_bypass
from xml.etree import ElementTree
import os
import re
import shutil

from PIL import Image, UnidentifiedImageError
from PyQt5.QtCore import QObject, QProcess, pyqtSignal


_SUFFIXES = {
    "AVIF": (".avif",), "BMP": (".bmp", ".dib"), "GIF": (".gif",), "ICO": (".ico",),
    "JPEG": (".jpg", ".jpeg", ".jpe", ".jfif"), "MPO": (".mpo", ".jpg", ".jpeg"),
    "JXL": (".jxl",), "PNG": (".png", ".apng"), "SVG": (".svg",),
    "TIFF": (".tiff", ".tif"), "WEBP": (".webp",),
}


def image_http_url(text):
    text = text.strip()
    if re.search(r"[\x00-\x20\x7f\\]", text):
        return None
    try:
        parts = urlsplit(text)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None:
            return None
        # Validate explicit ports before invoking curl.
        _ = parts.port
    except ValueError:
        return None
    return parts._replace(fragment="").geturl()


def _image_kind(path):
    try:
        with Image.open(path) as image:
            return image.format
    except (UnidentifiedImageError, Image.DecompressionBombError):
        with path.open("rb") as body:
            prefix = body.read(48)
            if prefix.startswith((b"\xff\x0a", b"\x00\x00\x00\x0cJXL \r\n\x87\n")):
                return "JXL"
            if prefix[4:8] == b"ftyp" and prefix[8:12] in (b"avif", b"avis"):
                return "AVIF"
            if prefix.lstrip().startswith((b"<", b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
                body.seek(0)
                try:
                    _, root = next(ElementTree.iterparse(body, events=("start",)))
                    if root.tag in {"svg", "{http://www.w3.org/2000/svg}svg"}:
                        return "SVG"
                except (ElementTree.ParseError, StopIteration):
                    pass
    return None


def _download_name(headers, url, suffixes):
    blocks = headers.replace(b"\r\n", b"\n").split(b"\n\n")
    final = next((block for block in reversed(blocks) if block.startswith(b"HTTP/")), b"")
    metadata = BytesHeaderParser(policy=policy.default).parsebytes(final.partition(b"\n")[2])
    url_name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    raw_name = (metadata.get_filename() or url_name).replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", raw_name).strip(" .")
    stem, suffix = os.path.splitext(name)
    if not stem:
        stem = "image"
    if re.fullmatch(r"CON|PRN|AUX|NUL|(?:COM|LPT)[1-9¹²³]", stem.split(".", 1)[0], re.I):
        stem = "_" + stem
    if suffix.lower() not in suffixes:
        suffix = suffixes[0]
    return (stem[:180].rstrip(" .") or "image") + suffix


def _publish_image(path, folder, name):
    stem, suffix = os.path.splitext(name)
    target = folder / name
    number = 1
    while True:
        try:
            # On Windows rename fails if the destination already exists.
            os.rename(path, target)
            return str(target)
        except FileExistsError:
            target = folder / f"{stem} ({number}){suffix}"
            number += 1


class BackgroundImageDownload(QObject):
    saved = pyqtSignal(str)
    failed = pyqtSignal(str)
    step_changed = pyqtSignal(str, str, bool, str)

    def __init__(self, parent, user_agent):
        super().__init__(parent)
        self._user_agent = user_agent
        self._queue = deque()
        self._job = None
        self._process = QProcess(self)
        self._process.started.connect(self._started)
        self._process.finished.connect(self._finished)
        self._process.errorOccurred.connect(self._process_error)

    @property
    def busy(self):
        return self._job is not None or bool(self._queue)

    def submit(self, url, folder, operation_id=""):
        self._queue.append((operation_id, url, Path(folder)))
        self._start_next()

    def stop(self):
        self._queue.clear()
        if self._process.state() != QProcess.NotRunning:
            self._process.kill()
            self._process.waitForFinished(1000)
        if self._job is not None:
            self._cleanup()

    def _start_next(self):
        while self._job is None and self._queue:
            operation_id, url, folder = self._queue.popleft()
            curl = shutil.which("curl.exe") or shutil.which("curl")
            if not curl:
                self._fail(operation_id, "process", "找不到 curl 下载程序。")
                continue
            try:
                temp = TemporaryDirectory(prefix=".image-download-", dir=folder, ignore_cleanup_errors=True)
            except OSError as error:
                code = getattr(error, "winerror", None) or error.errno
                self._fail(operation_id, "process", f"无法在速存目录创建临时文件（{code}）。")
                continue
            self._job = (operation_id, url, folder, temp)
            root = Path(temp.name)
            args = [
                "--disable", "--silent", "--show-error", "--fail", "--location", "--globoff", "--compressed",
                "--proto", "=http,https", "--proto-redir", "=http,https",
                "--connect-timeout", "20", "--max-time", "120",
                "--user-agent", self._user_agent, "--header", "Accept: image/*,*/*;q=0.8",
                "--dump-header", str(root / "headers"), "--output", str(root / "image"),
                "--write-out", "%{http_code}\n%{url_effective}",
            ]
            parts = urlsplit(url)
            host = parts.hostname
            for domains, referer in (
                (("pximg.net",), "https://www.pixiv.net/"),
                (("wixmp.com", "deviantart.net", "deviantart.com"), "https://www.deviantart.com/"),
            ):
                if any(host == domain or host.endswith("." + domain) for domain in domains):
                    args += ["--referer", referer]
                    break
            if not proxy_bypass(host):
                proxy = getproxies().get(parts.scheme)
                if proxy:
                    args += ["--proxy", proxy]
            self._process.start(curl, args + ["--", url])

    def _report(self, operation_id, step, passed, detail):
        if operation_id:
            self.step_changed.emit(operation_id, step, passed, detail)

    def _fail(self, operation_id, step, message):
        self._report(operation_id, step, False, message)
        self.failed.emit(message)

    def _started(self):
        if self._job is not None:
            self._report(self._job[0], "process", True, f"curl PID {self._process.processId()}")

    def _cleanup(self):
        _, _, _, temp = self._job
        self._job = None
        temp.cleanup()

    def _process_error(self, error):
        if error == QProcess.FailedToStart and self._job is not None:
            operation_id = self._job[0]
            self._cleanup()
            self._fail(operation_id, "process", f"无法启动 curl 下载程序。{self._process.errorString()}")
            self._start_next()

    def _finished(self, exit_code, exit_status):
        if self._job is None:
            return
        operation_id, original_url, folder, temp = self._job
        output = bytes(self._process.readAllStandardOutput()).decode("utf-8", "replace")
        self._process.readAllStandardError()
        http_code, _, effective_url = output.partition("\n")
        http_code = http_code.strip()
        has_http_code = len(http_code) == 3 and http_code.isascii() and http_code.isdigit() and http_code != "000"
        http = f"HTTP {http_code}" if has_http_code else "未收到 HTTP 状态"
        path, error = "", ""
        step = "transfer"
        try:
            if exit_status != QProcess.NormalExit or exit_code != 0:
                error = f"下载未完成（curl {exit_code}，{http}）。"
            else:
                self._report(operation_id, step, True, http)
                step = "validate"
                root = Path(temp.name)
                body = root / "image"
                kind = _image_kind(body)
                suffixes = _SUFFIXES.get(kind) or ()
                if suffixes:
                    self._report(operation_id, step, True, f"{kind} · {body.stat().st_size} 字节")
                    step = "save"
                    name = _download_name((root / "headers").read_bytes(), effective_url.strip() or original_url, suffixes)
                    path = _publish_image(body, folder, name)
                    self._report(operation_id, step, True, path)
                else:
                    error = "链接返回的内容无法识别为图片。"
        except (OSError, ValueError, SyntaxError) as exc:
            label = "无法写入速存目录" if step == "save" else "无法读取下载的图片"
            code = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
            error = f"{label}（系统错误 {code}）。" if code is not None else f"{label}（{type(exc).__name__}）。"
        finally:
            self._cleanup()
        if path:
            self.saved.emit(path)
        else:
            self._fail(operation_id, step, error)
        self._start_next()
