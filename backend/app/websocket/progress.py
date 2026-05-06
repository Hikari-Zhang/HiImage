"""
WebSocket 进度管理器 - 实时推送处理进度
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import List

router = APIRouter()


class ProgressManager:
    """管理 WebSocket 连接，广播进度更新"""

    def __init__(self):
        self.connections: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.connections:
            self.connections.remove(ws)

    async def broadcast(self, data: dict):
        """广播消息到所有连接"""
        disconnected = []
        for ws in self.connections:
            try:
                await ws.send_json(data)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.disconnect(ws)

    async def send_progress(self, percent: int, message: str = ""):
        """发送进度更新"""
        await self.broadcast({
            "type": "progress",
            "percent": percent,
            "message": message,
        })

    async def send_complete(self, message: str = "处理完成"):
        """发送完成消息"""
        await self.broadcast({
            "type": "complete",
            "percent": 100,
            "message": message,
        })

    async def send_error(self, message: str):
        """发送错误消息"""
        await self.broadcast({
            "type": "error",
            "percent": -1,
            "message": message,
        })


# 全局单例
progress_manager = ProgressManager()


@router.websocket("/ws/progress")
async def websocket_progress(ws: WebSocket):
    """WebSocket 进度端点"""
    await progress_manager.connect(ws)
    try:
        while True:
            # 保持连接（等待客户端关闭）
            await ws.receive_text()
    except WebSocketDisconnect:
        progress_manager.disconnect(ws)
