"""视频页只探测依赖。解析或下载时才导入 yt-dlp。"""

from functools import lru_cache
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec


HAS_YTDLP = find_spec("yt_dlp") is not None
HAS_YTDLP_EJS = find_spec("yt_dlp_ejs") is not None
try:
    YTDLP_VER = version("yt-dlp") if HAS_YTDLP else ""
except PackageNotFoundError:
    YTDLP_VER = "?"


@lru_cache(maxsize=1)
def load_ytdlp():
    import yt_dlp as backend

    try:
        import yt_dlp_ejs  # noqa: F401
    except ImportError:
        pass
    try:
        import yt_dlp.update as update

        youtube_dl = import_module("yt_dlp.YoutubeDL")
    except ImportError:
        return backend
    original = getattr(update, "_get_system_deprecation", None)
    if not callable(original) or getattr(original, "_desktop_assistant_patched", False):
        return backend

    def without_python_version_notice():
        message = original()
        if message and "Python version" in message and "has been deprecated" in message:
            return None
        return message

    without_python_version_notice._desktop_assistant_patched = True
    update._get_system_deprecation = without_python_version_notice
    youtube_dl._get_system_deprecation = without_python_version_notice
    return backend
