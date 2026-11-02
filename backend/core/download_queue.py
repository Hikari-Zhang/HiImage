"""单进程下载队列：按实际工作退出释放槽位，按下载实例隔离事件。"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
import logging
from pathlib import Path
import time
from typing import Any, AsyncIterator, Optional
from uuid import uuid4

from core.constants import DownloadStatus as DS, Provider, ConfigKey
from core.model_checker import invalidate_model_cache
from core.model_files import model_resource_paths
from core.model_resources import model_resources

logger = logging.getLogger("download_queue")


@dataclass(eq=False)
class DownloadTask:
    model_id: str
    model_name: str
    status: str = DS.QUEUED
    position: int = 0
    message: str = "等待下载..."
    speed: str = ""
    downloaded: str = ""
    total_size: str = ""
    created_at: float = field(default_factory=time.monotonic)
    updated_at: float = field(default_factory=time.monotonic)
    download_id: str = field(default_factory=lambda: uuid4().hex)
    _cancel_flag: bool = field(default=False, repr=False)
    _future: Any = field(default=None, repr=False)
    _runner: Any = field(default=None, repr=False)
    _config: dict = field(default_factory=dict, repr=False)
    _resources: tuple[Path, ...] = field(default=(), repr=False)

    def to_dict(self) -> dict:
        return {
            "modelId": self.model_id, "modelName": self.model_name,
            "downloadId": self.download_id,
            "status": self.status, "position": self.position,
            "message": self.message, "speed": self.speed,
            "downloaded": self.downloaded, "totalSize": self.total_size,
        }


class DownloadQueue:
    """仅由服务事件循环操作；线程只通过 call_soon_threadsafe 上报进度。"""

    def __init__(self) -> None:
        # _tasks 指向最新实例，_active 可保留已请求取消但尚未退出的旧实例。
        self._tasks: dict[str, DownloadTask] = {}
        self._active: dict[str, DownloadTask] = {}
        self._pending: deque[DownloadTask] = deque()
        # 订阅按 download_id 隔离，旧任务只能结束自己的连接。
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    @property
    def max_concurrent(self) -> int:
        from app.config import get as get_config
        return int(get_config(ConfigKey.DOWNLOAD_MAX_CONCURRENT, 3))

    def submit(self, model_id: str) -> DownloadTask:
        from copy import deepcopy
        from core.model_registry import MODEL_BY_ID
        existing = self._tasks.get(model_id)
        if existing and existing.status in (DS.QUEUED, DS.DOWNLOADING):
            return existing
        cfg = deepcopy(MODEL_BY_ID[model_id])
        task = DownloadTask(model_id, cfg.get("name", model_id), _config=cfg,
                            _resources=tuple(model_resource_paths(cfg)))
        self._tasks[model_id] = task
        self._pending.append(task)
        self._dispatch_pending()
        return task

    def bulk_submit(self, model_ids: list[str]) -> list[DownloadTask]:
        return [self.submit(mid) for mid in model_ids]

    def cancel(self, model_id: str, download_id: str | None = None) -> bool:
        task = self._tasks.get(model_id)
        if not task or (download_id is not None and task.download_id != download_id):
            return False
        if task.status not in (DS.QUEUED, DS.DOWNLOADING):
            return False
        task._cancel_flag = True
        task.status = DS.CANCELLED
        task.updated_at = time.monotonic()
        task.position = 0
        task.speed = ""
        task.message = "正在取消，等待下载线程退出" if self._active.get(model_id) is task else "已取消"
        self._broadcast(task)
        self._close_subscribers(task)
        if task in self._pending:
            self._pending.remove(task)
        # 不取消 executor Future，也不提前移除实际占用槽位的实例。
        self._dispatch_pending()
        return True

    def get_task(self, model_id: str) -> Optional[DownloadTask]:
        return self._tasks.get(model_id)

    def list_active(self) -> list[DownloadTask]:
        """包含取消后尚未退出的工作实例，以及等待中的新实例。"""
        return [*self._active.values(), *self._pending]

    async def subscribe(self, model_id: str) -> AsyncIterator[dict]:
        task = self._tasks.get(model_id)
        key = task.download_id if task else f"missing:{model_id}"
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(key, []).append(queue)
        # 终态快照补齐属于 DL-02；此处只改为绑定具体下载实例。
        if task and task.status in (DS.QUEUED, DS.DOWNLOADING):
            queue.put_nowait(task.to_dict())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    if event is None:
                        break
                    yield event
                except asyncio.TimeoutError:
                    yield {"heartbeat": True}
        finally:
            subscribers = self._subscribers.get(key, [])
            if queue in subscribers:
                subscribers.remove(queue)
            if not subscribers:
                self._subscribers.pop(key, None)

    def _dispatch_pending(self) -> None:
        # 可跳过正在等待共享资源的任务，使无关仓库利用空槽。
        for task in list(self._pending):
            if len(self._active) >= self.max_concurrent:
                break
            if task.model_id in self._active or any(
                a.is_relative_to(b) or b.is_relative_to(a)
                for active in self._active.values()
                for a in task._resources for b in active._resources
            ):
                continue
            self._pending.remove(task)
            self._active[task.model_id] = task
            task.status = DS.DOWNLOADING
            task.position = 0
            task.message = "准备下载..."
            task.updated_at = time.monotonic()
            self._broadcast(task)
            task._runner = asyncio.create_task(self._run_download(task))
        for position, task in enumerate(self._pending, 1):
            task.position = position
            task.message = f"排队中，第 {position} 位"
            self._broadcast(task)

    async def _run_download(self, task: DownloadTask) -> None:
        cancelled = False
        with model_resources.use():
            try:
                if not task._cancel_flag:
                    await self._download(task)
                task.status = DS.CANCELLED if task._cancel_flag else DS.DONE
                task.message = "已取消" if task._cancel_flag else "下载完成"
            except asyncio.CancelledError:
                cancelled = True
                task._cancel_flag = True
                task.status = DS.CANCELLED
                task.message = "已取消"
            except Exception as exc:
                task.status = DS.CANCELLED if task._cancel_flag else DS.ERROR
                task.message = "已取消" if task._cancel_flag else f"下载失败: {str(exc)[:200]}"
                logger.exception("下载失败: %s (%s)", task.model_id, task.download_id)
            finally:
                task.updated_at = time.monotonic()
                task.speed = ""
                self._broadcast(task)
                self._close_subscribers(task)
                # 只有该槽位的拥有者能够释放，不能移除重试实例。
                if self._active.get(task.model_id) is task:
                    del self._active[task.model_id]
                invalidate_model_cache(task.model_id)
                if not cancelled:
                    self._dispatch_pending()
        if cancelled:
            raise asyncio.CancelledError

    async def _download(self, task: DownloadTask) -> None:
        cfg = task._config
        # 暂保留依赖同步下载及失败策略，异步化与必需依赖失败传播属于 DL-03。
        if cfg.get("dependencies"):
            from huggingface_hub import snapshot_download
            from core.paths import get_hf_hub_dir
            for dep in cfg["dependencies"]:
                if task._cancel_flag:
                    return
                try:
                    snapshot_download(repo_id=dep, cache_dir=str(get_hf_hub_dir()))
                except Exception:
                    logger.exception("依赖下载失败: %s", dep)
        if task._cancel_flag:
            return
        from core.downloaders import download_rembg, download_hf, download_hf_multi, download_direct
        if cfg.get("provider") == Provider.REMBG:
            function = download_rembg
        elif cfg.get("hf_models"):
            function = download_hf_multi
        elif cfg.get("hf_model_id"):
            function = download_hf
        elif cfg.get("local_path") and cfg.get("download_url"):
            function = download_direct
        else:
            raise ValueError(f"无下载来源: {task.model_id}")

        loop = asyncio.get_running_loop()
        last_broadcast = 0.0

        def update(data):
            nonlocal last_broadcast
            if task._cancel_flag or self._tasks.get(task.model_id) is not task:
                return
            for key in ("speed", "downloaded", "total_size", "message"):
                setattr(task, key, data.get(key, ""))
            task.updated_at = time.monotonic()
            if task.updated_at - last_broadcast >= 0.5:
                last_broadcast = task.updated_at
                self._broadcast(task)

        def report(data):
            if not task._cancel_flag:
                loop.call_soon_threadsafe(update, data)

        def work():
            # 实际线程始终持有删除保护；在取消后的阻塞调用退出前不释放。
            with model_resources.use():
                if not task._cancel_flag:
                    function(cfg, progress_cb=report, cancel_check=lambda: task._cancel_flag)

        future = task._future = loop.run_in_executor(None, work)
        cancelled = False
        error = None
        while True:
            try:
                await asyncio.shield(future)
                break
            except asyncio.CancelledError:
                cancelled = True
                task._cancel_flag = True
            except Exception as exc:
                error = exc
                break
        if cancelled:
            raise asyncio.CancelledError
        if error is not None:
            raise error
        if not task._cancel_flag:
            try:
                from core.model_setup import ensure_iopaint_hub_links
                ensure_iopaint_hub_links(task.model_id)
            except Exception:
                logger.exception("IOPaint 缓存桥接失败: %s", task.model_id)

    def _broadcast(self, task: DownloadTask) -> None:
        for queue in self._subscribers.get(task.download_id, []):
            queue.put_nowait(task.to_dict())

    def _close_subscribers(self, task: DownloadTask) -> None:
        for queue in self._subscribers.get(task.download_id, []):
            queue.put_nowait(None)


_queue_instance: Optional[DownloadQueue] = None


def get_download_queue() -> DownloadQueue:
    global _queue_instance
    if _queue_instance is None:
        _queue_instance = DownloadQueue()
    return _queue_instance
