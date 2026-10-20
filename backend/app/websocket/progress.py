"""
WebSocket 进度管理器 - 实时推送处理进度

每个任务通过必填 task_id 维护独立频道，互不干扰。
"""
from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.websocket.origin import require_websocket_origin

router = APIRouter()

class ProgressManager:
    """
    管理 WebSocket 连接，按 task_id 隔离进度推送。

    推理进度只能发送到非空 task_id 对应的频道。
    """

    def __init__(self) -> None:
        self._channels: Dict[str, List[WebSocket]] = {}

    # ──────────────────────────────────────────────────────────
    # 连接管理
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def _validate_task_id(task_id: str) -> str:
        normalized = task_id.strip()
        if not normalized or len(normalized) > 128:
            raise ValueError("task_id must contain 1-128 non-whitespace characters")
        return normalized

    async def connect(self, ws: WebSocket, task_id: str) -> None:
        task_id = self._validate_task_id(task_id)
        await ws.accept()
        self._channels.setdefault(task_id, []).append(ws)

    def disconnect(self, ws: WebSocket, task_id: str) -> None:
        channel = self._channels.get(task_id)
        if channel is None:
            return
        try:
            channel.remove(ws)
        except ValueError:
            pass
        if not channel:
            self._channels.pop(task_id, None)

    # ──────────────────────────────────────────────────────────
    # 内部发送
    # ──────────────────────────────────────────────────────────

    async def _send_to_channel(self, task_id: str, data: dict) -> None:
        """向指定频道内所有连接发送消息，自动清理断开的连接。"""
        sockets = list(self._channels.get(task_id, []))
        if not sockets:
            return
        dead: List[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws, task_id)

    # ──────────────────────────────────────────────────────────
    # 公共 API
    # ──────────────────────────────────────────────────────────

    async def send_progress(
        self, percent: int, message: str = "", *, task_id: str
    ) -> None:
        """发送指定任务的进度更新。"""
        task_id = self._validate_task_id(task_id)
        data = {"type": "progress", "percent": percent, "message": message}
        await self._send_to_channel(task_id, data)

    async def send_complete(
        self, message: str = "处理完成", *, task_id: str
    ) -> None:
        """发送完成消息。"""
        task_id = self._validate_task_id(task_id)
        data = {"type": "complete", "percent": 100, "message": message}
        await self._send_to_channel(task_id, data)

    async def send_error(
        self, message: str, *, task_id: str
    ) -> None:
        """发送错误消息。"""
        task_id = self._validate_task_id(task_id)
        data = {"type": "error", "percent": -1, "message": message}
        await self._send_to_channel(task_id, data)


# 全局单例
progress_manager = ProgressManager()


@router.websocket("/ws/progress")
async def websocket_progress(
    ws: WebSocket,
    task_id: str = Query(..., min_length=1, max_length=128),
) -> None:
    """
    WebSocket 进度端点。

    用法：
      ws://host/api/ws/progress?task_id=xxx  # 仅接收指定任务的进度
    """
    try:
        task_id = progress_manager._validate_task_id(task_id)
    except ValueError:
        await ws.close(code=1008, reason="Invalid task_id")
        return
    if not await require_websocket_origin(ws):
        return
    await progress_manager.connect(ws, task_id)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        progress_manager.disconnect(ws, task_id)
