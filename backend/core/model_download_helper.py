"""
模型下载辅助工具

提供统一的接口，用于在执行操作前检查并下载所需的模型。

主要函数：
  - ensure_model_ready(model_id, progress_callback=None) -> (bool, str)
    检查模型是否已下载，如果未下载则触发下载并等待完成

用法：
  from core.model_download_helper import ensure_model_ready

  # 在操作前检查模型
  success, error_msg = await ensure_model_ready(model_id, progress_callback)
  if not success:
      return error_response(error_msg)

  # 继续执行操作...
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Callable, Optional, Tuple

from core.model_checker import ModelChecker, ModelCheckResult
from core.model_registry import MODEL_BY_ID
from core.constants import ModelStatus as MS, DownloadStatus as DS
from core.download_queue import get_download_queue

logger = logging.getLogger("model_download_helper")


async def _call_progress_callback(
    callback: Optional[Callable[..., Any]],
    percent: int,
    message: str,
) -> None:
    """
    调用进度回调函数，支持同步和异步回调。
    
    :param callback: 进度回调函数（同步或异步）
    :param percent: 进度百分比
    :param message: 进度消息
    """
    if not callback:
        return
    
    result = callback(percent, message)
    
    # 如果回调返回协程（异步函数），则等待完成
    if inspect.iscoroutine(result):
        try:
            await result
        except Exception as e:
            logger.warning(f"进度回调执行失败: {e}")


async def ensure_model_ready(
    model_id: str,
    progress_callback: Optional[Callable[..., Any]] = None,
    timeout: int = 1800,  # 30 minutes
) -> Tuple[bool, str]:
    """
    确保模型已下载并就绪。
    
    检查模型是否已下载，如果未下载则触发下载并等待完成。
    
    :param model_id: 模型 ID
    :param progress_callback: 进度回调函数，签名为 (percent: int, message: str) -> None
    :param timeout: 下载超时时间（秒），默认 30 分钟
    :return: (success, error_message)
              success=True  表示模型已就绪
              success=False 表示模型下载失败或出错，error_message 包含错误信息
    
    用法：
        success, error_msg = await ensure_model_ready(model_id, progress_callback)
        if not success:
            return JSONResponse(
                status_code=422,
                content={"detail": error_msg, "message": error_msg}
            )
        # 继续执行操作...
    """
    # 1. 检查模型是否存在于注册表
    cfg = MODEL_BY_ID.get(model_id)
    if not cfg:
        error_msg = f"未知模型 ID: {model_id!r}"
        logger.error(error_msg)
        return False, error_msg
    
    name = cfg.get("name", model_id)
    logger.info(f"[ensure_model_ready] 检查模型: {name} (id={model_id})")
    
    # 2. 检查模型是否已下载
    checker = ModelChecker()
    check_result: ModelCheckResult = checker.check_model(model_id)
    
    logger.info(f"[ensure_model_ready] 模型状态: {check_result.status} - {check_result.message}")
    
    # 如果模型已就绪，直接返回
    if check_result.status == MS.OK:
        logger.info(f"[ensure_model_ready] 模型已就绪: {name}")
        await _call_progress_callback(progress_callback, 100, f"模型已就绪: {name}")
        return True, ""
    
    # IOPaint CLI 模式模型：由 iopaint 在首次使用时自动下载，无需手动触发
    from core.constants import Provider, IOPaintMode
    if cfg.get("provider") == Provider.IOPAINT and cfg.get("iopaint_mode") == IOPaintMode.CLI:
        logger.info(f"[ensure_model_ready] IOPaint CLI 模型将在首次使用时自动下载: {name}")
        await _call_progress_callback(progress_callback, 100, f"模型将自动下载: {name}")
        return True, ""
    
    # 3. 模型未下载或下载不完整，触发下载
    logger.info(f"[ensure_model_ready] 模型未就绪，开始下载: {name} (status={check_result.status})")
    await _call_progress_callback(progress_callback, 0, f"开始下载模型: {name}")
    
    queue = get_download_queue()
    
    # 提交下载任务（自动去重，如果已在队列中则返回现有任务）
    task = queue.submit(model_id)
    logger.info(f"[ensure_model_ready] 下载任务已提交: {name} status={task.status}")
    
    # 4. 等待下载完成（轮询方式）
    try:
        success, error_msg = await _wait_for_download_completion_poll(
            model_id=model_id,
            queue=queue,
            progress_callback=progress_callback,
            model_name=name,
            timeout=timeout,
        )
        
        if success:
            logger.info(f"[ensure_model_ready] 模型下载完成: {name}")
            await _call_progress_callback(progress_callback, 100, f"模型下载完成: {name}")
        else:
            logger.error(f"[ensure_model_ready] 模型下载失败: {name} - {error_msg}")
            await _call_progress_callback(progress_callback, -1, f"模型下载失败: {error_msg}")
        
        return success, error_msg
        
    except asyncio.TimeoutError:
        error_msg = f"模型下载超时（{timeout}秒）: {name}"
        logger.error(f"[ensure_model_ready] {error_msg}")
        await _call_progress_callback(progress_callback, -1, error_msg)
        return False, error_msg
    except Exception as e:
        error_msg = f"模型下载过程出错: {name} - {str(e)}"
        logger.error(f"[ensure_model_ready] {error_msg}", exc_info=True)
        await _call_progress_callback(progress_callback, -1, error_msg)
        return False, error_msg


async def _wait_for_download_completion_poll(
    model_id: str,
    queue: "DownloadQueue",
    progress_callback: Optional[Callable[..., Any]],
    model_name: str,
    timeout: int,
) -> Tuple[bool, str]:
    """
    等待下载完成（轮询方式）。
    
    通过轮询下载队列的任务状态来等待下载完成。
    
    :param model_id: 模型 ID
    :param queue: 下载队列实例
    :param progress_callback: 进度回调函数
    :param model_name: 模型名称（用于日志）
    :param timeout: 超时时间（秒）
    :return: (success, error_message)
    """
    start_time = asyncio.get_event_loop().time()
    
    while True:
        # 检查超时
        elapsed = asyncio.get_event_loop().time() - start_time
        if elapsed > timeout:
            raise asyncio.TimeoutError()
        
        # 获取任务状态
        task = queue.get_task(model_id)
        
        if task is None:
            # 任务不存在，可能已完成并被清理
            logger.warning(f"[_wait_for_download_completion_poll] 任务不存在: {model_name}")
            return True, ""
        
        status = task.status
        message = task.message
        
        logger.debug(
            f"[_wait_for_download_completion_poll] {model_name} "
            f"status={status} message={message}"
        )
        
        # 更新进度
        if progress_callback and status == DS.DOWNLOADING:
            percent = _parse_progress_percent(message)
            await _call_progress_callback(progress_callback, percent, f"下载中: {model_name} - {message}")
        
        # 检查终态
        if status == DS.DONE:
            return True, ""
        elif status == DS.ERROR:
            return False, message or "下载失败"
        elif status == DS.CANCELLED:
            return False, "下载已取消"
        
        # 等待一段时间后再次检查
        await asyncio.sleep(1)


def _parse_progress_percent(message: str) -> int:
    """
    从进度消息中解析百分比。
    
    :param message: 进度消息，如 "file.zip 50%"
    :return: 进度百分比（0-100），解析失败返回 50
    """
    try:
        if "%" in message:
            # 尝试提取百分比
            parts = message.split("%")[0].split()
            percent_str = parts[-1]
            return int(percent_str)
    except (ValueError, IndexError):
        pass
    
    # 无法解析时返回默认值
    if "准备下载" in message:
        return 10
    elif "下载中" in message or "%" in message:
        return 50
    elif "下载完成" in message:
        return 100
    
    return 50
