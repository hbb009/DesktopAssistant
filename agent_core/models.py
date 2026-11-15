from dataclasses import asdict, dataclass
import threading

from .errors import Cancelled


@dataclass(frozen=True)
class ModelConfig:
    model: str
    base_url: str = 'http://127.0.0.1:11434'
    num_ctx: int = 8192
    num_predict: int = 1024
    chunk_chars: int = 1600
    temperature: float = 0.2
    read_timeout: float = 120.0

    def __post_init__(self):
        if not self.model.strip():
            raise ValueError('必须指定模型。')
        if not 2048 <= self.num_ctx <= 32768:
            raise ValueError('第一阶段上下文须在2048～32768之间；不会自动升档。')
        if not 64 <= self.num_predict <= min(4096, self.num_ctx // 2):
            raise ValueError('输出预算须在64～4096之间，且不超过上下文一半。')
        if not 100 <= self.chunk_chars <= 8000:
            raise ValueError('阅读片段须在100～8000字符之间。')
        if not 1 <= self.read_timeout <= 600:
            raise ValueError('响应超时须在1～600秒之间。')

    def to_dict(self):
        return asdict(self)


class Cancellation:
    """Cooperative cancellation plus callbacks to interrupt a blocked socket."""
    def __init__(self):
        self.event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks = []

    def check(self):
        if self.event.is_set():
            raise Cancelled('任务已暂停；未提交步骤将在恢复时重新执行。')

    def cancel(self):
        with self._lock:
            self.event.set()
            callbacks = list(self._callbacks)
        for callback in callbacks:
            try:
                callback()
            except OSError:
                pass

    def add_callback(self, callback):
        with self._lock:
            self._callbacks.append(callback)
            cancelled = self.event.is_set()
        if cancelled:
            callback()

    def remove_callback(self, callback):
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)
