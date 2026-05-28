"""
WebSocket 进度管理器 - 实时推送处理进度

每个任务通过 task_id 维护独立频道，互不干扰。
客户端连接时携带 ?task_id=<uuid>，不传则订阅全局广播（向后兼容）。
"""
from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

# 全局频道 key（向后兼容：未传 task_id 的旧客户端订阅此频道）
_GLOBAL_CHANNEL = ""


class ProgressManager:
    """
    管理 WebSocket 连接，按 task_id 隔离进度推送。

    - task_id 非空：仅向订阅该 task_id 的连接推送
    - task_id 为空：向所有连接广播（向后兼容）
    """

    def __init__(self) -> None:
        # task_id -> [WebSocket, ...]
        # _GLOBAL_CHANNEL ("") 用于未传 task_id 的旧客户端
        self._channels: Dict[str, List[WebSocket]] = {}

    # ──────────────────────────────────────────────────────────
    # 连接管理
    # ──────────────────────────────────────────────────────────

    async def connect(self, ws: WebSocket, task_id: str = _GLOBAL_CHANNEL) -> None:
        await ws.accept()
        self._channels.setdefault(task_id, []).append(ws)

    def disconnect(self, ws: WebSocket, task_id: str = _GLOBAL_CHANNEL) -> None:
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

    async def broadcast(self, data: dict) -> None:
        """向所有已连接的客户端广播（不区分 task_id）。"""
        for tid in list(self._channels):
            await self._send_to_channel(tid, data)

    async def send_progress(
        self, percent: int, message: str = "", task_id: str = _GLOBAL_CHANNEL
    ) -> None:
        """发送进度更新。task_id 非空时仅推送到该任务频道。"""
        data = {"type": "progress", "percent": percent, "message": message}
        if task_id:
            await self._send_to_channel(task_id, data)
        else:
            await self.broadcast(data)

    async def send_complete(
        self, message: str = "处理完成", task_id: str = _GLOBAL_CHANNEL
    ) -> None:
        """发送完成消息。"""
        data = {"type": "complete", "percent": 100, "message": message}
        if task_id:
            await self._send_to_channel(task_id, data)
        else:
            await self.broadcast(data)

    async def send_error(
        self, message: str, task_id: str = _GLOBAL_CHANNEL
    ) -> None:
        """发送错误消息。"""
        data = {"type": "error", "percent": -1, "message": message}
        if task_id:
            await self._send_to_channel(task_id, data)
        else:
            await self.broadcast(data)


# 全局单例
progress_manager = ProgressManager()


@router.websocket("/ws/progress")
async def websocket_progress(ws: WebSocket, task_id: str = _GLOBAL_CHANNEL) -> None:
    """
    WebSocket 进度端点。

    用法：
      ws://host/api/ws/progress              # 全局广播（向后兼容）
      ws://host/api/ws/progress?task_id=xxx  # 仅接收指定任务的进度
    """
    await progress_manager.connect(ws, task_id)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        progress_manager.disconnect(ws, task_id)
