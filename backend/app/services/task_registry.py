"""有界、进程内任务状态，成功结果可暂存在独立文件缓存。"""
import math
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable, Literal
from app.services.task_results import TaskResults


ACTIVE = {"queued", "running"}
TaskStatus = Literal["queued", "running", "succeeded", "failed", "interrupted"]


class TaskConflict(Exception):
    pass


class TaskCapacityFull(Exception):
    pass


@dataclass
class TaskRecord:
    task_id: str
    operation: str
    status: TaskStatus
    progress: float
    message: str
    created_at: float
    updated_at: float
    finished_at: float | None = None
    http_status: int | None = None
    result_available: bool = False


class TaskRegistry:
    def __init__(self, max_records: int = 200, ttl_seconds: float = 600,
                 clock: Callable[[], float] = time.time, result_store: TaskResults | None = None):
        if max_records < 1 or ttl_seconds <= 0:
            raise ValueError("task retention must be positive")
        self._records: dict[str, TaskRecord] = {}
        self._lock = threading.RLock()
        self._max_records = max_records
        self._ttl = ttl_seconds
        self._clock = clock
        self.results = result_store or TaskResults(ttl_seconds=ttl_seconds, clock=clock)

    def _expire(self, now: float) -> None:
        expired = [key for key, record in self._records.items()
                   if record.finished_at is not None and now - record.finished_at >= self._ttl]
        for key in expired:
            self.results.remove(key)
            del self._records[key]

    def register(self, task_id: str, operation: str, *, queued: bool) -> None:
        with self._lock:
            now = self._clock()
            self._expire(now)
            if task_id in self._records:
                raise TaskConflict(task_id)
            if len(self._records) >= self._max_records:
                terminal = [record for record in self._records.values() if record.status not in ACTIVE]
                if not terminal:
                    raise TaskCapacityFull()
                oldest = min(terminal, key=lambda record: record.finished_at)
                self.results.remove(oldest.task_id)
                del self._records[oldest.task_id]
            self._records[task_id] = TaskRecord(
                task_id, operation, "queued" if queued else "running", 0,
                "等待执行" if queued else "正在执行", now, now,
            )

    def get(self, task_id: str) -> dict | None:
        with self._lock:
            self._expire(self._clock())
            record = self._records.get(task_id)
            if record is None:
                return None
            snapshot = asdict(record)
            snapshot["result_available"] = record.status == "succeeded" and self.results.has(task_id)
            return snapshot

    def save_result(self, task_id: str, body: bytes) -> bool:
        with self._lock:
            record = self._records.get(task_id)
            if not record or record.status not in ACTIVE:
                return False
        return self.results.put(task_id, body)

    def mark_running(self, task_id: str) -> None:
        with self._lock:
            record = self._records.get(task_id)
            if record and record.status == "queued":
                record.status = "running"
                record.message = "正在执行"
                record.updated_at = self._clock()

    def progress(self, task_id: str, percent: float, message: str) -> None:
        if not math.isfinite(percent) or not 0 <= percent <= 100:
            return
        with self._lock:
            record = self._records.get(task_id)
            if record and record.status == "running":
                record.progress = percent
                record.message = message[:500]
                record.updated_at = self._clock()

    def finish(self, task_id: str, status: str, http_status: int | None = None) -> None:
        if status not in {"succeeded", "failed", "interrupted"}:
            raise ValueError("invalid terminal status")
        with self._lock:
            record = self._records.get(task_id)
            if record and record.status in ACTIVE:
                record.status = status
                record.http_status = http_status
                record.message = {"succeeded": "处理完成", "failed": "任务失败，请检查请求或服务日志",
                                  "interrupted": "请求执行中断，结果不可恢复"}[status]
                if status == "succeeded":
                    record.progress = 100
                record.finished_at = record.updated_at = self._clock()

    def discard(self, task_id: str) -> None:
        with self._lock:
            self.results.remove(task_id)
            self._records.pop(task_id, None)


task_registry = TaskRegistry()
