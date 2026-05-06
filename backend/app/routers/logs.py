"""
日志路由 - 获取历史日志 + WebSocket 实时推送
"""
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from app.logging_manager import log_manager

router = APIRouter()


@router.get("/logs")
async def get_logs(
    level: Optional[str] = Query(None, description="过滤级别: DEBUG/INFO/WARNING/ERROR"),
    limit: int = Query(100, description="最大条数"),
):
    """获取历史日志"""
    if level:
        entries = log_manager.get_filtered(level=level.upper(), limit=limit)
    else:
        entries = log_manager.get_filtered(limit=limit)
    return {"logs": entries, "total": len(entries)}


@router.get("/logs/errors")
async def get_error_logs():
    """只获取 ERROR 级别日志"""
    entries = log_manager.get_errors()
    return {"logs": entries, "total": len(entries)}


@router.delete("/logs")
async def clear_logs():
    """清空日志"""
    log_manager.clear()
    return {"status": "ok", "message": "日志已清空"}


@router.websocket("/ws/logs")
async def websocket_logs(ws: WebSocket):
    """WebSocket 实时日志推送"""
    await log_manager.ws_connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        log_manager.ws_disconnect(ws)
