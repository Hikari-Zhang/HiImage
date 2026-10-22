"""在应用层将算法回调适配到任务进度频道。core 不依赖 WebSocket。"""
import asyncio
from concurrent.futures import CancelledError
from typing import Callable

from app.logging_manager import log_manager
from app.websocket.progress import progress_manager


def make_progress_callback(
    task_id: str,
    loop: asyncio.AbstractEventLoop,
    source: str = "inpaint",
) -> Callable[[int, str], None]:
    def report_failure(error: BaseException) -> None:
        log_manager.error(f"进度上报失败: {error}", source=source)

    def callback(percent: int, message: str) -> None:
        # The coroutine must be closed when scheduling is rejected by a closed loop.
        update = progress_manager.send_progress(percent, message, task_id=task_id)
        try:
            future = asyncio.run_coroutine_threadsafe(update, loop)
        except Exception as error:
            update.close()
            report_failure(error)
            return

        def completed(result):
            try:
                result.result()
            except CancelledError:
                pass
            except Exception as error:
                report_failure(error)

        future.add_done_callback(completed)

    return callback
