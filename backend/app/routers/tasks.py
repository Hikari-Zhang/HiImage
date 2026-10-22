"""任务状态查询；响应不包含图像、请求参数或异常正文。"""
from typing import Annotated
import asyncio

from fastapi import APIRouter, HTTPException, Path, Response

from app.services.task_registry import task_registry, TaskRecord
from app.task_id import TaskId

router = APIRouter()


@router.get("/tasks/{task_id}/result")
async def get_task_result(task_id: Annotated[TaskId, Path()]):
    headers = {"Cache-Control": "no-store"}
    record = await asyncio.to_thread(task_registry.get, task_id)
    if record is None:
        raise HTTPException(404, "任务不存在或记录已过期", headers=headers)
    if record["status"] in {"queued", "running"}:
        raise HTTPException(409, "任务尚未结束，请稍后取回结果", headers=headers)
    if record["status"] != "succeeded":
        raise HTTPException(409, "任务未成功完成，没有可取回的结果", headers=headers)
    body = await asyncio.to_thread(task_registry.results.read, task_id)
    if body is None:
        raise HTTPException(410, "结果未保存、已过期或已被清理", headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


@router.get("/tasks/{task_id}", response_model=TaskRecord)
async def get_task(task_id: Annotated[TaskId, Path()], response: Response):
    response.headers["Cache-Control"] = "no-store"
    record = await asyncio.to_thread(task_registry.get, task_id)
    if record is None:
        raise HTTPException(404, "任务不存在或记录已过期", headers={"Cache-Control": "no-store"})
    return record
