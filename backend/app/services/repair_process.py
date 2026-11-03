"""在既有调度槽内监督一次修复进程。"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import numpy as np
from fastapi.responses import JSONResponse

from app.config import get
from app.logging_manager import log_manager
from app.repair_worker import PROGRESS_PREFIX
from core.model_registry import get_model
from core.process_runner import ProcessCancelled, ProcessCleanupError, _cli_log_directory, _stream_cli_output


class RepairTimeout(RuntimeError):
    pass


class RepairOutOfMemory(RuntimeError):
    pass


def error_response(error):
    if isinstance(error, RepairOutOfMemory):
        return JSONResponse(status_code=503, content={
            "code": "INFERENCE_OUT_OF_MEMORY", "message": str(error), "retryable": False,
        })
    if isinstance(error, RepairTimeout):
        return JSONResponse(status_code=504, content={
            "code": "INFERENCE_TIMEOUT", "message": str(error), "retryable": False,
        })
    if isinstance(error, ProcessCleanupError):
        return JSONResponse(status_code=503, content={
            "code": "INFERENCE_CLEANUP_FAILED", "message": str(error), "retryable": False,
        })
    return None


def _worker_command(directory):
    return [sys.executable, "-u", "-m", "app.repair_worker", str(directory)]


def _run_process(operation, image, mask, rois, options, timeout, stop, progress):
    deadline = time.monotonic() + timeout
    with _cli_log_directory() as name:
        directory = Path(name)
        np.save(directory / "image.npy", image, allow_pickle=False)
        if mask is not None:
            np.save(directory / "mask.npy", mask, allow_pickle=False)
        (directory / "request.json").write_text(
            json.dumps({"operation": operation, "rois": rois, "options": options}), encoding="utf-8",
        )
        pending = ""
        diagnostic = ""
        cancel_deadline = None

        def cancelled():
            nonlocal cancel_deadline
            if not stop.is_set():
                return False
            if cancel_deadline is None:
                (directory / "cancel").touch()
                cancel_deadline = time.monotonic() + 0.5
            return time.monotonic() >= cancel_deadline

        def consume(chunk):
            nonlocal pending, diagnostic
            diagnostic = (diagnostic + chunk)[-4096:]
            lines = (pending + chunk).split("\n")
            pending = lines.pop()[-8192:]
            for line in lines:
                if line.startswith(PROGRESS_PREFIX) and progress is not None:
                    try:
                        percent, message = json.loads(line[len(PROGRESS_PREFIX):])
                    except (ValueError, TypeError):
                        continue
                    if type(percent) is int and 0 <= percent <= 100 and isinstance(message, str):
                        progress(percent, message)

        try:
            if stop.is_set():
                raise ProcessCancelled("修复已取消")
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired([], timeout)
            code = _stream_cli_output(
                _worker_command(directory),
                {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONIOENCODING": "utf-8"},
                timeout, consume, cwd=Path(__file__).resolve().parents[2],
                cancelled=cancelled, confirm_group=True, deadline=deadline,
            )
            if stop.is_set():
                raise ProcessCancelled("修复已取消")
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired([], timeout)
            if code:
                log_manager.error(f"修复进程退出码 {code}\n{diagnostic}", source="repair_worker")
                if (directory / "out_of_memory").is_file():
                    raise RepairOutOfMemory("修复内存不足，请释放显存或缩小选区后重试")
                raise RuntimeError("修复进程执行失败，请查看后端日志")
            result_path = directory / "result.npy"
            max_bytes = int(get("image.max_output_mb", 256)) * 1024 * 1024
            if not result_path.is_file() or result_path.stat().st_size > max_bytes + 4096:
                raise RuntimeError("修复进程结果缺失或超出输出预算")
            result = np.load(result_path, allow_pickle=False)
            if (result.dtype != np.uint8 or result.ndim != 3 or result.shape[2] != 3
                    or not result.size or result.nbytes > max_bytes):
                raise RuntimeError("修复进程返回了无效的图像")
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired([], timeout)
            return result
        except subprocess.TimeoutExpired as exc:
            raise RepairTimeout(f"修复超过 {timeout:g} 秒") from exc


async def run_repair(scheduler, operation, image, *, mask=None, rois=None,
                     options, progress_callback=None, request=None):
    model_id = options["model_id"] if operation == "inpaint" else options["inpaint_model"]
    config = get_model(model_id)
    timeout = 1800 if config["executor"] == "diffusers" else 300
    if operation == "inpaint":
        options = {**options, "rois": rois}
    stop = threading.Event()

    def supervise():
        try:
            return _run_process(operation, image, mask, rois, options, timeout, stop, progress_callback)
        except ProcessCleanupError:
            scheduler.fail_process_cleanup()
            raise

    work = asyncio.create_task(scheduler.run_blocking(supervise))

    async def watch_disconnect():
        while not await request.is_disconnected():
            await asyncio.sleep(0.1)
        owner.cancel()

    owner = asyncio.current_task()
    watcher = asyncio.create_task(watch_disconnect()) if request is not None else None
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        stop.set()
        # 保持调度槽和权重使用许可，直到监督线程确认进程已回收。
        while not work.done():
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not work.cancelled() and isinstance(work.exception(), ProcessCleanupError):
            raise work.exception()
        raise
    finally:
        if watcher is not None:
            watcher.cancel()
