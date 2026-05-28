"""
核心工具函数（供 core 和 app 层共同使用）。

原本在 app/routers/models.py 和 core/download_queue.py 中各自定义了一份，
现提取到此处，消除重复代码和反向依赖。
"""
import asyncio
from typing import Callable


def fmt_speed(bytes_per_sec: float) -> str:
    """将字节/秒格式化为人类可读字符串。"""
    if bytes_per_sec >= 1024 * 1024:
        return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"
    elif bytes_per_sec >= 1024:
        return f"{bytes_per_sec / 1024:.0f} KB/s"
    return f"{bytes_per_sec:.0f} B/s"


def fmt_size(total_bytes: int) -> str:
    """将字节数格式化为人类可读字符串。"""
    if total_bytes >= 1024 * 1024 * 1024:
        return f"{total_bytes / (1024**3):.1f} GB"
    elif total_bytes >= 1024 * 1024:
        return f"{total_bytes / (1024**2):.0f} MB"
    elif total_bytes >= 1024:
        return f"{total_bytes / 1024:.0f} KB"
    return f"{total_bytes} B"


def resolve_effective_device(model_name: str, device: str) -> str:
    """
    查询 models.yaml 中模型的 device_override 字段，返回实际使用的设备名。

    查找顺序：
      1. 按 model_name 直接匹配 MODEL_BY_ID
      2. 按 iopaint_model_id 字段回退匹配（兼容 IOPaint server 模式的模型 ID）
      3. 均未找到或无 device_override 时，返回原始 device

    用法：
        from core.utils import resolve_effective_device
        device = resolve_effective_device("wm_lama", "mps")
    """
    try:
        from core.model_registry import MODEL_BY_ID, MODELS  # lazy import 避免循环依赖
        cfg = MODEL_BY_ID.get(model_name)
        if cfg is None:
            # 按 iopaint_model_id 回退查找
            for m in MODELS:
                if m.get("iopaint_model_id") == model_name:
                    cfg = m
                    break
        if cfg:
            override = cfg.get("device_override")
            if override:
                return override
    except Exception:
        pass
    return device


def make_progress_callback(
    task_id: str,
    loop: asyncio.AbstractEventLoop,
    source: str = "inpaint",
) -> Callable[[int, str], None]:
    """
    创建线程安全的进度回调（子线程 → 主线程事件循环）。

    用法：
        callback = make_progress_callback(req.task_id, loop, source="synthesis")
        model_exec.execute(image, progress_callback=callback)
    """
    # Import lazily to avoid circular import (app → core → app)
    from app.websocket.progress import progress_manager
    from app.logging_manager import log_manager

    def callback(percent: int, message: str) -> None:
        try:
            asyncio.run_coroutine_threadsafe(
                progress_manager.send_progress(percent, message, task_id=task_id),
                loop,
            )
        except Exception as e:
            log_manager.error(f"进度上报失败: {e}", source=source)

    return callback
