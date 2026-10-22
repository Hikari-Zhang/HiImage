"""有界临时结果缓存。文件名独立于 task_id，不接收服务器文件路径。"""
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from core.paths import TMP_DIR


@dataclass
class _Result:
    path: Path
    size: int
    expires_at: float


class TaskResults:
    def __init__(self, *, base_dir: Path = TMP_DIR, max_item_bytes: int = 64 * 1024 * 1024,
                 max_total_bytes: int = 256 * 1024 * 1024, ttl_seconds: float = 600,
                 clock: Callable[[], float] = time.time):
        if min(max_item_bytes, max_total_bytes, ttl_seconds) <= 0:
            raise ValueError("result cache limits must be positive")
        self._base_dir = base_dir
        self._max_item = max_item_bytes
        self._max_total = max_total_bytes
        self._ttl = ttl_seconds
        self._clock = clock
        self._directory = None
        self._entries: dict[str, _Result] = {}
        self._lock = threading.RLock()

    def remove(self, task_id: str) -> None:
        with self._lock:
            item = self._entries.get(task_id)
            if item:
                try:
                    item.path.unlink(missing_ok=True)
                except OSError:
                    return  # Still count files that the OS could not remove.
                del self._entries[task_id]

    def _expire(self) -> None:
        now = self._clock()
        for key, item in list(self._entries.items()):
            if now >= item.expires_at:
                self.remove(key)

    def put(self, task_id: str, body: bytes) -> bool:
        if not body or len(body) > min(self._max_item, self._max_total):
            return False
        with self._lock:
            self._expire()
            self.remove(task_id)
            if task_id in self._entries:
                return False
            for key in list(self._entries):
                if sum(item.size for item in self._entries.values()) + len(body) <= self._max_total:
                    break
                self.remove(key)
            if sum(item.size for item in self._entries.values()) + len(body) > self._max_total:
                return False
            path = None
            try:
                if self._directory is None:
                    self._base_dir.mkdir(parents=True, exist_ok=True)
                    self._directory = tempfile.TemporaryDirectory(prefix="hiimage-task-results-", dir=self._base_dir)
                path = Path(self._directory.name) / (uuid.uuid4().hex + ".json")
                path.write_bytes(body)
            except OSError:
                if path:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
                return False
            self._entries[task_id] = _Result(path, len(body), self._clock() + self._ttl)
            return True

    def has(self, task_id: str) -> bool:
        with self._lock:
            self._expire()
            item = self._entries.get(task_id)
            return bool(item and item.expires_at > self._clock() and item.path.is_file())

    def read(self, task_id: str) -> bytes | None:
        with self._lock:
            if not self.has(task_id):
                return None
            try:
                return self._entries[task_id].path.read_bytes()
            except OSError:
                self.remove(task_id)
                return None
