"""
下载队列调度器 —— 全局单例，统一管理所有模型下载任务。

职责：
  - 接收下载请求（submit / bulk_submit）
  - 去重：同一 model_id 已在队列/下载中则直接返回现有任务
  - 并发控制：同时下载数量不超过 max_concurrent（读配置）
  - 排队等待：超出并发数的任务进入 pending 队列，槽位释放后自动开始
  - 取消：支持取消 queued 或 downloading 状态的任务
  - 订阅：支持多个 SSE 客户端同时订阅同一个 model_id 的状态变更

配置项（config/settings.json）：
  download.max_concurrent  最大并发下载数，默认 3
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

from core.constants import DownloadStatus as DS, Provider, ConfigKey
from core.utils import fmt_speed, fmt_size
from core.model_checker import invalidate_model_cache

logger = logging.getLogger("download_queue")


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class DownloadTask:
    """单个下载任务的状态快照。"""
    model_id: str
    model_name: str
    status: str = DS.QUEUED          # queued / downloading / done / error / cancelled
    position: int = 0               # queued 时的排队序号（1-based），downloading 时为 0
    message: str = "等待下载..."
    speed: str = ""
    downloaded: str = ""
    total_size: str = ""
    created_at: float = field(default_factory=time.monotonic)
    updated_at: float = field(default_factory=time.monotonic)

    # 内部控制（不序列化给前端）
    _cancel_flag: bool = field(default=False, repr=False)
    _future: Any = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {
            "modelId":   self.model_id,
            "modelName": self.model_name,
            "status":    self.status,
            "position":  self.position,
            "message":   self.message,
            "speed":     self.speed,
            "downloaded": self.downloaded,
            "totalSize": self.total_size,
        }


# ── 全局下载队列 ──────────────────────────────────────────────────────────────

class DownloadQueue:
    """
    全局下载队列（单例）。

    用法：
        queue = get_download_queue()
        task = queue.submit("wm_lama")
        async for event in queue.subscribe("wm_lama"):
            yield event
    """

    def __init__(self) -> None:
        # 活跃任务（downloading）
        self._active: Dict[str, DownloadTask] = {}
        # 等待队列（queued），按入队顺序
        self._pending: deque[str] = deque()
        # 所有任务（包含 done/error/cancelled，用于状态查询）
        self._tasks: Dict[str, DownloadTask] = {}
        # 每个 model_id 的订阅者队列列表（支持多个 SSE 客户端）
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}
        # 调度事件（延迟初始化，确保在事件循环存在时创建）
        self._slot_event: Optional[asyncio.Event] = None
        # 调度协程是否已启动
        self._scheduler_started = False

    def _get_slot_event(self) -> asyncio.Event:
        """延迟获取/创建 slot_event，确保绑定到正确的事件循环。"""
        if self._slot_event is None:
            self._slot_event = asyncio.Event()
            logger.debug("[队列] asyncio.Event 已初始化")
        return self._slot_event

    @property
    def max_concurrent(self) -> int:
        from app.config import get as get_config
        return int(get_config(ConfigKey.DOWNLOAD_MAX_CONCURRENT, 3))

    # ── 公开接口 ─────────────────────────────────────────────────────────────

    def submit(self, model_id: str) -> DownloadTask:
        """
        提交单个模型下载任务。

        - 已在 active/pending 中 → 直接返回现有任务（去重）
        - 有空槽 → 立即开始（会在事件循环下一轮调度）
        - 无空槽 → 进入 pending 队列排队
        """
        # 去重：已有活跃任务（queued 或 downloading）则直接返回
        existing = self._tasks.get(model_id)
        if existing and existing.status in (DS.QUEUED, DS.DOWNLOADING):
            logger.info(f"[队列] submit 去重: {model_id} 已是 {existing.status}，直接返回")
            return existing

        from core.model_registry import MODEL_BY_ID
        cfg = MODEL_BY_ID.get(model_id, {})
        name = cfg.get("name", model_id)

        logger.info(f"[队列] submit 开始: model_id={model_id!r} name={name!r} cfg_keys={list(cfg.keys())}")

        task = DownloadTask(model_id=model_id, model_name=name)
        self._tasks[model_id] = task

        active_count = len(self._active)
        max_c = self.max_concurrent
        logger.info(f"[队列] 当前并发: {active_count}/{max_c}")

        if active_count < max_c:
            # 有空槽，直接放入 active 并启动下载
            task.status = DS.DOWNLOADING
            task.position = 0
            task.message = "准备下载..."
            self._active[model_id] = task
            logger.info(f"[队列] 直接开始下载: {name} → 调度 _run_download")
            # 在事件循环中启动下载协程
            fut = asyncio.ensure_future(self._run_download(model_id))
            logger.info(f"[队列] asyncio.ensure_future 已提交: {fut}")
        else:
            # 排队
            self._pending.append(model_id)
            task.position = len(self._pending)
            task.status = DS.QUEUED
            task.message = f"排队中，第 {task.position} 位"
            logger.info(f"[队列] 排队等待: {name} position={task.position}")

        # 确保调度协程正在运行
        self._ensure_scheduler()
        # 触发调度
        self._get_slot_event().set()

        return task

    def bulk_submit(self, model_ids: List[str]) -> List[DownloadTask]:
        """批量提交下载任务（一键下载入口）。返回所有任务（含已去重的现有任务）。"""
        tasks = []
        for mid in model_ids:
            tasks.append(self.submit(mid))
        logger.info(f"[队列] 批量提交: {len(model_ids)} 个模型")
        return tasks

    def cancel(self, model_id: str) -> bool:
        """取消下载任务。返回是否成功取消。"""
        task = self._tasks.get(model_id)
        if not task:
            return False

        if task.status == DS.QUEUED:
            # 从 pending 队列移除
            if model_id in self._pending:
                self._pending.remove(model_id)
            task.status = DS.CANCELLED
            task.message = "已取消"
            self._broadcast(model_id, task.to_dict())
            self._update_positions()
            logger.info(f"[队列] 取消排队任务: {task.model_name}")
            return True

        if task.status == DS.DOWNLOADING:
            # 设置取消标志，下载线程会检查
            task._cancel_flag = True
            task.status = DS.CANCELLED
            task.message = "已取消"
            self._broadcast(model_id, task.to_dict())
            if model_id in self._active:
                del self._active[model_id]
            self._slot_event.set()
            logger.info(f"[队列] 取消下载中任务: {task.model_name}")
            return True

        return False

    def get_task(self, model_id: str) -> Optional[DownloadTask]:
        """查询指定模型的任务状态。"""
        return self._tasks.get(model_id)

    def list_active(self) -> List[DownloadTask]:
        """返回所有 queued + downloading 的任务，按位置/创建时间排序。"""
        result = list(self._active.values())
        for mid in self._pending:
            t = self._tasks.get(mid)
            if t:
                result.append(t)
        return result

    async def subscribe(self, model_id: str) -> AsyncIterator[dict]:
        """
        SSE 订阅：持续推送指定 model_id 的状态变更事件。

        使用单一 while 循环，每次超时只发送心跳后继续等待，
        不递归调用其他方法，避免长时间运行后栈溢出。

        用法（在路由中）：
            async for event in queue.subscribe(model_id):
                yield _sse("status", event)
        """
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(model_id, []).append(q)
        logger.info(f"[subscribe] 客户端连接: {model_id!r}, 当前订阅数={len(self._subscribers.get(model_id, []))}")

        # 先推一次当前状态（让客户端立即同步），但只推活跃状态
        # done/error/cancelled 是终态，不应该在重新连接时重新触发
        task = self._tasks.get(model_id)
        if task and task.status in (DS.QUEUED, DS.DOWNLOADING):
            logger.info(f"[subscribe] 推送初始状态: {model_id!r} status={task.status!r}")
            await q.put(task.to_dict())
        elif task:
            logger.info(f"[subscribe] 跳过推送终态: {model_id!r} status={task.status!r}")
        else:
            logger.info(f"[subscribe] 无历史任务: {model_id!r}")

        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30)
                    if event is None:  # None 是终止信号
                        logger.info(f"[subscribe] 收到终止信号，关闭: {model_id!r}")
                        break
                    logger.debug(f"[subscribe] 推送事件: {model_id!r} status={event.get('status')!r}")
                    yield event
                except asyncio.TimeoutError:
                    # 心跳：只发送心跳，继续循环，不递归
                    yield {"heartbeat": True}
        finally:
            subs = self._subscribers.get(model_id, [])
            if q in subs:
                subs.remove(q)

    # ── 内部调度 ─────────────────────────────────────────────────────────────

    def _ensure_scheduler(self) -> None:
        """确保调度协程正在运行（只启动一次）。"""
        if not self._scheduler_started:
            self._scheduler_started = True
            asyncio.ensure_future(self._scheduler())
            logger.info("[队列] 调度协程已启动")

    async def _scheduler(self) -> None:
        """后台调度协程：持续检查是否有空槽和等待任务。"""
        logger.info("[队列] 调度协程进入主循环")
        while True:
            self._get_slot_event().clear()

            # 把 pending 中的任务填满空槽
            while self._pending and len(self._active) < self.max_concurrent:
                mid = self._pending.popleft()
                task = self._tasks.get(mid)
                if not task or task.status == DS.CANCELLED:
                    continue
                task.status = DS.DOWNLOADING
                task.position = 0
                task.message = "准备下载..."
                self._active[mid] = task
                self._update_positions()
                self._broadcast(mid, task.to_dict())
                logger.info(f"[队列] 调度器启动下载: {task.model_name}")
                asyncio.ensure_future(self._run_download(mid))

            # 等待下一次触发（有任务完成或新任务提交）
            await self._get_slot_event().wait()

    async def _run_download(self, model_id: str) -> None:
        """在线程池中执行下载，通过 asyncio.Queue 接收进度。"""
        from core.model_registry import MODEL_BY_ID

        logger.info(f"[_run_download] 进入: model_id={model_id!r}")

        task = self._tasks.get(model_id)
        if not task:
            logger.error(f"[_run_download] 找不到 task: {model_id!r}")
            return

        cfg = dict(MODEL_BY_ID.get(model_id, {}))  # 浅拷贝，避免修改全局配置
        # 构造取消检查函数，作为独立参数传递给下载函数（不污染 cfg 字典）
        cancel_check = lambda: task._cancel_flag
        logger.info(f"[_run_download] cfg 字段: provider={cfg.get('provider')!r}, hf_model_id={cfg.get('hf_model_id')!r}, local_path={cfg.get('local_path')!r}, download_url={cfg.get('download_url')!r}")

        # 下载依赖模型（dependencies 字段）
        deps = cfg.get("dependencies", [])
        if deps:
            from huggingface_hub import snapshot_download
            from core.paths import get_hf_hub_dir
            cache_dir = str(get_hf_hub_dir())
            for dep_id in deps:
                if task._cancel_flag:
                    break
                try:
                    snapshot_download(repo_id=dep_id, cache_dir=cache_dir)
                    logger.info(f"[_run_download] 依赖已下载: {dep_id}")
                except Exception as dep_e:
                    logger.warning(f"[_run_download] 依赖下载失败 {dep_id}: {dep_e}")

        loop = asyncio.get_event_loop()
        progress_queue: asyncio.Queue = asyncio.Queue()

        def _put(data: dict) -> None:
            if not task._cancel_flag:
                loop.call_soon_threadsafe(progress_queue.put_nowait, data)

        # 选择下载方式
        provider = cfg.get("provider", "")
        try:
            from core.downloaders import download_rembg, download_hf, download_hf_multi, download_direct

            # 构造下载函数闭包，将 cancel_check 作为独立参数传递
            def _make_download_fn(fn, _cfg=cfg, _cb=_put, _cc=cancel_check):
                return lambda: fn(_cfg, progress_cb=_cb, cancel_check=_cc)

            if provider == Provider.REMBG:
                logger.info(f"[_run_download] 使用 rembg 下载: {task.model_name}")
                download_fn = _make_download_fn(download_rembg)
            elif cfg.get("hf_models"):
                logger.info(f"[_run_download] 使用 HF 组合模型下载: {task.model_name} ({len(cfg['hf_models'])} 个子模型)")
                download_fn = _make_download_fn(download_hf_multi)
            elif cfg.get("hf_model_id"):
                logger.info(f"[_run_download] 使用 HF 下载: {task.model_name} repo={cfg['hf_model_id']!r}")
                download_fn = _make_download_fn(download_hf)
            elif cfg.get("local_path") and cfg.get("download_url"):
                logger.info(f"[_run_download] 使用直接下载: {task.model_name} url={cfg['download_url']!r}")
                download_fn = _make_download_fn(download_direct)
            else:
                logger.error(f"[_run_download] 无下载来源: model_id={model_id!r} cfg={dict(cfg)}")
                raise ValueError(f"无下载来源: {model_id}")

            future = loop.run_in_executor(None, download_fn)
        except Exception as e:
            logger.error(f"[_run_download] 启动下载失败: {e}", exc_info=True)
            # 启动失败，直接进 finally
            task.status = DS.ERROR
            task.message = f"启动下载失败: {str(e)[:200]}"
            task.updated_at = time.monotonic()
            # 释放槽位
            if model_id in self._active:
                del self._active[model_id]
            self._get_slot_event().set()
            return

        # ── future 已提交，下面消费进度并等待完成 ────────────────────────────
        task._future = future
        logger.info(f"[_run_download] future 已提交，开始等待完成: {task.model_name}")

        last_broadcast = 0.0  # 上次 broadcast 时间，用于节流

        # 持续消费进度，直到 future 结束或被取消
        while not future.done():
            if task._cancel_flag:
                future.cancel()
                break
            await asyncio.sleep(0.2)
            while not progress_queue.empty():
                item = progress_queue.get_nowait()
                if isinstance(item, dict):
                    task.speed = item.get("speed", "")
                    task.downloaded = item.get("downloaded", "")
                    task.total_size = item.get("total_size", "")
                    task.message = item.get("message", "")
                    task.updated_at = time.monotonic()
                    # 节流：至少间隔 0.5s 才 broadcast 一次
                    now = time.monotonic()
                    if now - last_broadcast >= 0.5:
                        last_broadcast = now
                        self._broadcast(model_id, task.to_dict())

        # 清空剩余进度（不再 broadcast，直接丢弃，最终状态在 finally 里推送）
        while not progress_queue.empty():
            progress_queue.get_nowait()

        if task._cancel_flag:
            # 已在 cancel() 中处理状态，直接返回
            task.status = DS.CANCELLED
            task.message = "已取消"
            task.updated_at = time.monotonic()
        else:
            exc = future.exception() if not future.cancelled() else None
            if exc:
                task.status = DS.ERROR
                task.message = f"下载失败: {str(exc)[:200]}"
                task.speed = ""
                task.updated_at = time.monotonic()
                logger.error(f"[队列] ✗ 失败: {task.model_name} — {exc}", exc_info=True)
            else:
                # 成功
                task.status = DS.DONE
                task.message = "下载完成"
                task.speed = ""
                task.updated_at = time.monotonic()
                logger.info(f"[队列] ✓ 完成: {task.model_name}")

                # 为 IOPaint server 模式模型补全 hub/ 软链接（使 iopaint 能扫描到本地缓存）
                try:
                    from core.model_checker import ensure_iopaint_hub_links
                    ensure_iopaint_hub_links(model_id)
                except Exception as _link_err:
                    logger.warning(f"[队列] iopaint hub 链接补全失败（非致命）: {_link_err}")

        # ── 统一收尾：broadcast + 释放槽位 + 触发调度 ─────────────────────────
        logger.info(f"[_run_download] 收尾: status={task.status!r} model={task.model_name!r}")
        self._broadcast(model_id, task.to_dict())
        self._close_subscribers(model_id)
        if model_id in self._active:
            del self._active[model_id]
        self._get_slot_event().set()
        try:
            invalidate_model_cache(model_id)
        except Exception as _cache_err:
            logger.warning(f"[_run_download] 缓存失效失败（非致命）: {_cache_err}")

    def _broadcast(self, model_id: str, event: dict) -> None:
        """向所有订阅指定 model_id 的客户端推送事件。"""
        for q in self._subscribers.get(model_id, []):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def _close_subscribers(self, model_id: str) -> None:
        """向订阅者发送终止信号（None）。"""
        for q in self._subscribers.get(model_id, []):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass

    def _update_positions(self) -> None:
        """重新计算 pending 队列中所有任务的排队位置。"""
        for idx, mid in enumerate(self._pending, 1):
            task = self._tasks.get(mid)
            if task and task.status == DS.QUEUED:
                task.position = idx
                task.message = f"排队中，第 {idx} 位"
                self._broadcast(mid, task.to_dict())


# ── 单例管理 ──────────────────────────────────────────────────────────────────

_queue_instance: Optional[DownloadQueue] = None


def get_download_queue() -> DownloadQueue:
    """获取全局下载队列单例。"""
    global _queue_instance
    if _queue_instance is None:
        _queue_instance = DownloadQueue()
        logger.info("[队列] 初始化全局下载队列")
    return _queue_instance
