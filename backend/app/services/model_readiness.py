"""检查模型就绪，并在应用层发布下载进度。"""
from app.task_id import TaskId
from app.websocket.progress import progress_manager
from app.logging_manager import log_manager


async def ensure_model_ready_with_progress(
    model_id: str, operation_name: str, task_id: TaskId, source: str = "inpaint"
) -> tuple[bool, str]:
    """
    检查并确保模型已下载，支持进度推送。

    :param model_id: 模型 ID
    :param operation_name: 操作名称（用于日志和进度消息）
    :param task_id: 任务 ID，用于隔离 WebSocket 频道
    :param source: 日志来源标识
    :return: (success, error_message)
    """
    from core.model_download_helper import ensure_model_ready

    async def _progress_callback(percent: int, message: str):
        try:
            if percent >= 0:
                await progress_manager.send_progress(
                    max(5, min(percent, 30)),
                    f"{operation_name}：{message}",
                    task_id=task_id,
                )
            else:
                await progress_manager.send_error(message, task_id=task_id)
        except Exception as e:
            log_manager.error(f"进度推送失败: {e}", source=source)

    log_manager.info(f"[{operation_name}] 检查模型就绪状态: {model_id}", source=source)
    success, error_msg = await ensure_model_ready(
        model_id=model_id,
        progress_callback=_progress_callback,
    )

    if not success:
        log_manager.error(f"[{operation_name}] 模型未就绪: {model_id} - {error_msg}", source=source)

    return success, error_msg
