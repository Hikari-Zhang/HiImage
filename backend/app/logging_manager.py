"""
日志管理模块 - 收集关键日志和所有 error，支持 WebSocket 实时推送
"""
import logging
import asyncio
from collections import deque
from datetime import datetime
from typing import List, Optional

from fastapi import WebSocket


class LogEntry:
    """单条日志记录"""

    def __init__(self, level: str, message: str, source: str = ""):
        self.timestamp = datetime.now().isoformat()
        self.level = level
        self.message = message
        self.source = source

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "level": self.level,
            "message": self.message,
            "source": self.source,
        }


class LogManager:
    """
    全局日志管理器
    - 保留最近 500 条日志（内存缓冲）
    - 关键日志（INFO 及以上）+ 所有 ERROR 必定保留
    - 支持 WebSocket 实时推送
    """

    MAX_ENTRIES = 500

    def __init__(self):
        self._entries: deque[LogEntry] = deque(maxlen=self.MAX_ENTRIES)
        self._ws_connections: List[WebSocket] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        """设置事件循环（在应用启动时调用）"""
        self._loop = loop

    def add(self, level: str, message: str, source: str = ""):
        """添加一条日志"""
        entry = LogEntry(level=level, message=message, source=source)
        self._entries.append(entry)

        # 异步推送给 WebSocket 客户端
        if self._loop and self._ws_connections:
            asyncio.run_coroutine_threadsafe(
                self._broadcast(entry.to_dict()), self._loop
            )

    def info(self, message: str, source: str = ""):
        self.add("INFO", message, source)

    def warning(self, message: str, source: str = ""):
        self.add("WARNING", message, source)

    def error(self, message: str, source: str = ""):
        self.add("ERROR", message, source)

    def debug(self, message: str, source: str = ""):
        self.add("DEBUG", message, source)

    def get_all(self) -> List[dict]:
        """获取所有日志"""
        return [e.to_dict() for e in self._entries]

    def get_errors(self) -> List[dict]:
        """只获取 ERROR 级别日志"""
        return [e.to_dict() for e in self._entries if e.level == "ERROR"]

    def get_filtered(self, level: Optional[str] = None, limit: int = 100) -> List[dict]:
        """获取过滤后的日志"""
        entries = list(self._entries)
        if level:
            entries = [e for e in entries if e.level == level]
        # 返回最新的 limit 条
        return [e.to_dict() for e in entries[-limit:]]

    def clear(self):
        """清空日志"""
        self._entries.clear()

    # WebSocket 相关
    async def ws_connect(self, ws: WebSocket):
        await ws.accept()
        self._ws_connections.append(ws)

    def ws_disconnect(self, ws: WebSocket):
        if ws in self._ws_connections:
            self._ws_connections.remove(ws)

    async def _broadcast(self, data: dict):
        """推送日志到所有 WebSocket 客户端"""
        disconnected = []
        for ws in self._ws_connections:
            try:
                await ws.send_json({"type": "log", "data": data})
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.ws_disconnect(ws)


# 全局单例
log_manager = LogManager()


class WebSocketLogHandler(logging.Handler):
    """
    Python logging Handler - 将标准 logging 输出接入 LogManager
    这样所有用 logging 模块输出的日志都会被收集
    """

    def __init__(self, manager: LogManager):
        super().__init__()
        self.manager = manager

    def emit(self, record: logging.LogRecord):
        level = record.levelname
        message = self.format(record)
        source = record.name
        self.manager.add(level, message, source)


def setup_logging():
    """
    配置全局 logging，将关键日志和所有 error 接入 LogManager
    """
    handler = WebSocketLogHandler(log_manager)
    handler.setLevel(logging.INFO)  # INFO 及以上都收集
    handler.setFormatter(logging.Formatter("%(message)s"))

    # 注册到根 logger
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    # 同时为 uvicorn/fastapi 的 logger 添加
    for logger_name in ["uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"]:
        logger = logging.getLogger(logger_name)
        logger.addHandler(handler)
