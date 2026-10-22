"""在图像路由边界记录任务，以实际 HTTP 结果确认终态。"""
import asyncio

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse
from pydantic import TypeAdapter

from app.services.task_registry import task_registry, TaskConflict, TaskCapacityFull
from app.shared_executor import inference_admission, InferenceQueueFull
from app.task_id import TaskId
from app.logging_manager import log_manager

TASK_ID_ADAPTER = TypeAdapter(TaskId)


class TaskRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        queued = any(dependency.dependency is inference_admission for dependency in self.dependencies)

        async def tracked(request: Request):
            if request.method != "POST":
                return await handler(request)
            # Leave invalid/missing IDs to the existing request validation contract.
            try:
                body = await request.json()
                task_id = TASK_ID_ADAPTER.validate_python(body.get("task_id") if isinstance(body, dict) else None)
            except ValueError:
                return await handler(request)
            try:
                task_registry.register(task_id, self.path, queued=queued)
            except TaskConflict:
                return JSONResponse(status_code=409, content={"code": "TASK_ID_CONFLICT", "message": "任务 ID 已存在，请查询状态或使用新 ID", "retryable": False})
            except TaskCapacityFull:
                return JSONResponse(status_code=503, content={"code": "TASK_REGISTRY_FULL", "message": "任务记录容量已满，请稍后重试", "retryable": True}, headers={"Retry-After": "1"})
            request.state.tracked_task_id = task_id
            try:
                response = await handler(request)
                # Keep serialized output only; no second Base64/JSON copy is retained in memory.
                if 200 <= response.status_code < 300 and not self.path.endswith('/detect'):
                    try:
                        await asyncio.to_thread(task_registry.save_result, task_id, response.body)
                    except Exception:
                        log_manager.warning("任务结果暂存失败，本次响应仍正常返回", source="task_results")
            except InferenceQueueFull:
                # Admission was rejected: keep the ID reusable for an explicit retry.
                task_registry.discard(task_id)
                raise
            except asyncio.CancelledError:
                task_registry.finish(task_id, "interrupted")
                raise
            except (HTTPException, RequestValidationError) as error:
                task_registry.finish(task_id, "failed", getattr(error, "status_code", 422))
                raise
            except BaseException:
                task_registry.finish(task_id, "failed", 500)
                raise
            task_registry.finish(task_id, "succeeded" if response.status_code < 400 else "failed", response.status_code)
            return response

        return tracked
