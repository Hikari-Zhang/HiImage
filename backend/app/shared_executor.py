"""单进程内共享的有界推理调度器。"""

import asyncio
import os
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable, Mapping, TypeVar

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.logging_manager import log_manager
from app.services.task_registry import task_registry
from core.model_resources import model_resources


DEFAULT_MAX_PENDING = 4
MIN_MAX_PENDING = 0
MAX_MAX_PENDING = 32
QUEUE_FULL_MESSAGE = "服务器任务队列已满，请稍后重试"
T = TypeVar("T")


class InferenceQueueFull(RuntimeError):
    """推理队列已满或调度器已停止接收任务。"""


def max_pending_from_env(environ: Mapping[str, str]) -> int:
    """读取等待队列上限；非法配置安全回退到默认值并记录清晰告警。"""
    raw = environ.get("HIIMAGE_MAX_PENDING_INFERENCE")
    if raw is None:
        return DEFAULT_MAX_PENDING
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if MIN_MAX_PENDING <= value <= MAX_MAX_PENDING:
        return value
    log_manager.warning(
        "HIIMAGE_MAX_PENDING_INFERENCE 必须是 0-32 的整数，"
        f"当前值 {raw!r} 非法，已回退为 {DEFAULT_MAX_PENDING}",
        source="inference_scheduler",
    )
    return DEFAULT_MAX_PENDING


class _Admission:
    def __init__(self, label: str, future: asyncio.Future[None] | None):
        self.label = label
        self.future = future
        self.running = future is None


class InferenceScheduler:
    """FIFO 调度 HTTP 推理任务，并用单线程执行所有阻塞调用。"""

    def __init__(self, max_pending: int = DEFAULT_MAX_PENDING):
        if not MIN_MAX_PENDING <= max_pending <= MAX_MAX_PENDING:
            raise ValueError("max_pending 必须在 0-32 之间")
        self._max_pending = max_pending
        self._capacity = max_pending + 1
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="hiimage-inference",
        )
        self._lock = threading.Lock()
        self._queue: deque[_Admission] = deque()
        self._accepting = True
        self._drain_waiters: list[tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]] = []
        self._executor_shutdown = False

    def snapshot(self) -> dict[str, int | bool]:
        """返回线程安全的队列状态快照。"""
        with self._lock:
            running = int(bool(self._queue and self._queue[0].running))
            return {
                "running": running,
                "pending": len(self._queue) - running,
                "capacity": self._capacity,
                "accepting": self._accepting,
            }

    def stop_accepting(self) -> None:
        """同步关闭接纳入口，使生命周期关闭开始即拒绝新任务。"""
        with self._lock:
            self._accepting = False

    def _reserve(self, label: str) -> _Admission:
        loop = asyncio.get_running_loop()
        with self._lock:
            if not self._accepting or len(self._queue) >= self._capacity:
                log_manager.warning(
                    f"拒绝推理任务: {label}",
                    source="inference_scheduler",
                )
                raise InferenceQueueFull()
            future = None if not self._queue else loop.create_future()
            admission = _Admission(label, future)
            self._queue.append(admission)
        log_manager.info(f"接纳推理任务: {label}", source="inference_scheduler")
        return admission

    def _release(self, admission: _Admission) -> None:
        next_admission = None
        drain_waiters = []
        with self._lock:
            try:
                self._queue.remove(admission)
            except ValueError:
                return
            if admission.running and self._queue:
                next_admission = self._queue[0]
                next_admission.running = True
            if not self._queue:
                drain_waiters = self._drain_waiters
                self._drain_waiters = []

        if next_admission is not None and next_admission.future is not None:
            future = next_admission.future
            future.get_loop().call_soon_threadsafe(
                lambda: None if future.done() else future.set_result(None)
            )
        for loop, waiter in drain_waiters:
            loop.call_soon_threadsafe(
                lambda current=waiter: None
                if current.done()
                else current.set_result(None)
            )
        log_manager.info(
            f"完成推理任务: {admission.label}",
            source="inference_scheduler",
        )

    @asynccontextmanager
    async def admission(self, label: str) -> AsyncIterator[None]:
        """立即预留容量；排队任务按 FIFO 等待成为当前任务。"""
        admission = self._reserve(label)
        try:
            if admission.future is not None:
                try:
                    await admission.future
                except asyncio.CancelledError:
                    self._release(admission)
                    raise
            with model_resources.use():
                yield
        finally:
            self._release(admission)

    async def run_blocking(
        self,
        function: Callable[..., T],
        *args,
        **kwargs,
    ) -> T:
        """在线程中执行阻塞调用，协程取消时等待底层调用结束再传播取消。"""
        loop = asyncio.get_running_loop()
        def protected_call():
            with model_resources.use():
                return function(*args, **kwargs)

        future = loop.run_in_executor(
            self._executor,
            protected_call,
        )
        cancelled = False
        result: T | None = None
        error: BaseException | None = None
        while True:
            try:
                result = await asyncio.shield(future)
            except asyncio.CancelledError:
                cancelled = True
                if future.done():
                    break
                continue
            except BaseException as exc:
                error = exc
            break

        if cancelled:
            raise asyncio.CancelledError
        if error is not None:
            raise error
        return result  # type: ignore[return-value]

    async def shutdown(self) -> None:
        """停止接收新任务，等待已接纳任务结束并清理工作线程。"""
        loop = asyncio.get_running_loop()
        waiter = None
        with self._lock:
            self._accepting = False
            if self._queue:
                waiter = loop.create_future()
                self._drain_waiters.append((loop, waiter))
        if waiter is not None:
            await asyncio.shield(waiter)

        await asyncio.to_thread(self.close_executor)

    def close_executor(self) -> None:
        """幂等关闭工作线程；供生命周期异常清理兜底。"""
        with self._lock:
            if self._executor_shutdown:
                return
            self._executor_shutdown = True
        self._executor.shutdown(wait=True, cancel_futures=False)


def install_inference_queue_handler(app: FastAPI) -> None:
    """安装统一的推理队列满响应。"""

    @app.exception_handler(InferenceQueueFull)
    async def _queue_full_handler(
        _request: Request,
        _error: InferenceQueueFull,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": "1"},
            content={
                "code": "INFERENCE_QUEUE_FULL",
                "message": QUEUE_FULL_MESSAGE,
                "retryable": True,
            },
        )


inference_scheduler = InferenceScheduler(max_pending_from_env(os.environ))


async def inference_admission(request: Request) -> AsyncIterator[None]:
    """FastAPI yield 依赖：一次 HTTP 推理任务只占用一个 admission。"""
    label = f"{request.method} {request.url.path}"
    async with inference_scheduler.admission(label):
        task_id = getattr(request.state, "tracked_task_id", None)
        if task_id:
            task_registry.mark_running(task_id)
        yield
