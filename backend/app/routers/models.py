"""
模型管理端点

GET    /api/models/health              → 检测所有注册模型的完整性
GET    /api/models/health/{model_id}   → 检测单个模型的完整性
GET    /api/models/list                → 返回所有注册模型的元数据（含当前状态）
GET    /api/models/groups              → 聚合接口：一次返回所有功能的模型分组

── 下载队列 API ────────────────────────────────────────────────────────────────
POST   /api/models/download/{model_id} → 提交单模型下载任务（返回任务状态）
POST   /api/models/download/bulk       → 批量提交（一键下载），返回任务列表
DELETE /api/models/download/{model_id} → 取消下载任务
GET    /api/models/subscribe/{model_id}→ SSE 订阅单个模型的实时状态变更
GET    /api/models/queue               → 查询当前队列中所有活跃任务

DELETE /api/models/{model_id}/files    → 删除指定模型的本地权重文件（不修改 models.yaml）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.services.model_catalog import model_groups
from core.model_checker import ModelChecker
from core.model_registry import MODELS, MODE_GROUPS
from core.constants import ModelStatus as MS, DownloadStatus as DS, Provider, IOPaintMode

logger = logging.getLogger("models")

router = APIRouter(prefix="/models", tags=["models"])


def _result_to_dict(r) -> dict:
    """将 ModelCheckResult 转为可序列化字典。"""
    return {
        "id":               r.model_id,
        "name":             r.name,
        "provider":         r.provider,
        "status":           r.status,
        "message":          r.message,
        "file_path":        r.file_path,
        "size_mb":          round(r.size_bytes / (1024 * 1024), 1) if r.size_bytes else None,
        "expected_size_mb": r.expected_size_mb,
    }


@router.get("/health")
async def models_health():
    """
    检测所有注册模型的文件完整性。

    返回格式：
    ```json
    {
      "models": [
        {"id": "birefnet", "status": "ok", "message": "100 MB", ...},
        {"id": "flux_fill", "status": "missing", "message": "未在 HF 缓存中找到 ...", ...}
      ],
      "summary": {"ok": 18, "missing": 6, "corrupted": 0, "unknown": 2, "total": 26}
    }
    ```
    """
    checker = ModelChecker()
    results = await asyncio.to_thread(checker.check_all)
    return {
        "models": [_result_to_dict(r) for r in results],
        "summary": {
            "ok":       sum(1 for r in results if r.status == MS.OK),
            "missing":  sum(1 for r in results if r.status in (MS.MISSING, MS.PARTIAL)),
            "corrupted": sum(1 for r in results if r.status == MS.CORRUPTED),
            "unknown":  sum(1 for r in results if r.status == MS.UNKNOWN),
            "total":    len(results),
        },
    }


@router.get("/health/{model_id}")
async def model_health(model_id: str):
    """
    检测单个模型的文件完整性。

    返回格式：
    ```json
    {"id": "birefnet", "status": "ok", "message": "100 MB", ...}
    ```
    """
    checker = ModelChecker()
    result = await asyncio.to_thread(checker.check_model, model_id)
    if result.status == MS.UNKNOWN and result.message.startswith("模型 ID"):
        raise HTTPException(status_code=404, detail=f"未知模型 ID: {model_id!r}")
    return _result_to_dict(result)


@router.get("/list")
async def models_list():
    """
    返回所有注册模型的元数据（来自 models.yaml），
    附带实时完整性状态。
    """
    checker = ModelChecker()
    check_map = {r.model_id: r for r in await asyncio.to_thread(checker.check_all)}

    result = []
    for m in MODELS:
        mid = m["id"]
        r = check_map.get(mid)
        result.append({
            **{k: v for k, v in m.items() if k not in ("supported_params",)},
            "status":  r.status if r else MS.UNKNOWN,
            "message": r.message if r else "",
        })

    return {
        "models": result,
        "mode_groups": MODE_GROUPS,
    }


# ── 下载队列 API ─────────────────────────────────────────────────────────────

def _sse(event: str, data: dict) -> str:
    """构造一条 SSE 消息。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/download/bulk")
async def queue_download_bulk():
    """
    批量提交所有缺失/损坏的模型到下载队列（一键下载入口）。

    - 自动检测所有缺失/损坏模型
    - 批量提交，已在队列中的自动去重
    - 返回本次提交的任务列表

    返回格式：
    ```json
    {
      "submitted": 5,
      "tasks": [{"modelId": ..., "status": "downloading"|"queued", ...}]
    }
    ```
    """
    from core.model_checker import ModelChecker
    from core.download_queue import get_download_queue

    checker = ModelChecker()
    loop = asyncio.get_running_loop()

    # 过滤可下载模型（排除 IOPaint cli 内置模型）
    downloadable = [
        m for m in MODELS
        if not (m.get("provider") == Provider.IOPAINT and m.get("iopaint_mode") == IOPaintMode.CLI)
    ]

    # 检测哪些模型需要下载
    results = await loop.run_in_executor(None, checker.check_all)
    status_map = {r.model_id: r.status for r in results}

    need_download = [
        m for m in downloadable
        if status_map.get(m["id"], MS.MISSING) != MS.OK
    ]

    queue = get_download_queue()
    tasks = queue.bulk_submit([m["id"] for m in need_download])

    return {
        "submitted": len(need_download),
        "tasks": [t.to_dict() for t in tasks],
    }


@router.post("/download/{model_id}")
async def queue_download_single(model_id: str):
    """
    提交单个模型下载任务到全局队列。

    - 去重：已在队列/下载中则返回现有任务状态
    - 有空槽则立即开始，否则排队等待

    返回格式：
    ```json
    {"modelId": "wm_lama", "modelName": "LaMa", "status": "downloading", "position": 0, ...}
    ```
    """
    from core.model_registry import MODEL_BY_ID
    cfg = MODEL_BY_ID.get(model_id)
    logger.info(f"[POST /download/{model_id}] 收到请求, cfg 存在={cfg is not None}")
    if not cfg:
        raise HTTPException(status_code=404, detail=f"未知模型 ID: {model_id!r}")

    if cfg.get("provider") == Provider.IOPAINT and cfg.get("iopaint_mode") == IOPaintMode.CLI:
        raise HTTPException(
            status_code=400,
            detail="IOPaint 内置模型将在首次使用时自动下载，无需手动触发"
        )

    from core.download_queue import get_download_queue
    queue = get_download_queue()
    task = queue.submit(model_id)
    logger.info(f"[POST /download/{model_id}] submit 返回: status={task.status!r}")
    return task.to_dict()


@router.delete("/download/{model_id}")
async def cancel_download(model_id: str):
    """
    取消指定模型的下载任务（queued 或 downloading 状态均可取消）。
    """
    from core.download_queue import get_download_queue
    queue = get_download_queue()
    success = queue.cancel(model_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"未找到活跃的下载任务: {model_id!r}")
    return {"ok": True, "message": f"已取消: {model_id}"}


@router.get("/subscribe/{model_id}")
async def subscribe_model(model_id: str, request: Request):
    """
    SSE 订阅：实时推送指定模型的下载状态变更。

    - 连接后立即推送当前状态
    - 每次状态变更（queued→downloading→done/error）推送一次
    - 任务完成/失败后服务端自动关闭连接
    - 支持多个客户端同时订阅同一模型

    事件类型：status
    """
    from core.download_queue import get_download_queue
    queue = get_download_queue()

    async def _generate():
        async for event in queue.subscribe(model_id):
            if await request.is_disconnected():
                break
            if event.get("heartbeat"):
                # 发送 SSE 注释作为心跳，保持连接
                yield ": heartbeat\n\n"
            else:
                yield _sse("status", event)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/queue")
async def get_queue_status():
    """
    查询当前下载队列中所有活跃任务（queued + downloading）。

    返回格式：
    ```json
    {
      "active": [...],
      "max_concurrent": 3
    }
    ```
    """
    from core.download_queue import get_download_queue
    queue = get_download_queue()
    return {
        "active": [t.to_dict() for t in queue.list_active()],
        "max_concurrent": queue.max_concurrent,
    }


@router.delete("/{model_id}/files")
async def delete_model_files(model_id: str):
    """仅删除独占、闲置权重；共享或占用时返回 409，不修改注册表。"""
    from core.model_registry import MODEL_BY_ID
    from core.model_files import shared_model_ids, delete_files
    from core.model_resources import model_resources, ModelResourcesBusy
    from core.model_checker import invalidate_model_cache
    from core.download_queue import get_download_queue

    cfg = MODEL_BY_ID.get(model_id)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"未知模型 ID: {model_id!r}")
    if cfg.get("provider") == Provider.IOPAINT and cfg.get("iopaint_mode") == IOPaintMode.CLI:
        return {"ok": True, "message": "此模型暂不支持通过文件删除接口清理"}

    try:
        # 同一次独占许可内检查和删除，避免检查后又开始使用的竞争窗口。
        # 此处没有 await；工作线程会在删除结束后才获得使用许可。
        with model_resources.deleting():
            shared = shared_model_ids(model_id, MODEL_BY_ID)
            if shared:
                affected = [{"id": mid, "name": MODEL_BY_ID[mid].get("name", mid)} for mid in shared]
                names = "、".join(f"{item['name']} ({item['id']})" for item in affected)
                raise HTTPException(status_code=409, detail={
                    "code": "MODEL_FILES_SHARED",
                    "message": f"权重被其他模型共用，拒绝删除。受影响模型：{names}",
                    "affected_models": affected,
                })
            if get_download_queue().list_active():
                raise ModelResourcesBusy("当前存在下载或排队任务，请结束后再删除模型文件")
            try:
                return delete_files(cfg)
            finally:
                # 包含部分删除后发生 I/O 错误的情形，不继续展示过期的 OK。
                invalidate_model_cache()
    except ModelResourcesBusy as exc:
        raise HTTPException(status_code=409, detail={
            "code": "MODEL_FILES_BUSY", "message": str(exc),
        }) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        logger.exception("删除模型文件失败: %s", model_id)
        raise HTTPException(status_code=500, detail="删除模型文件失败，请检查文件占用和权限后重试") from exc


@router.get("/groups")
async def get_model_groups():
    """
    聚合接口：一次返回所有功能的模型分组，替代三次并发请求。

    返回格式：
    ```json
    {
      "inpaint": { "groups": [{"label": "...", "models": [...]}] },
      "upscale": { "groups": [{"label": "...", "models": [...]}] },
      "cutout":  { "groups": [{"label": "...", "models": [...]}], "default_model": "birefnet" }
    }
    ```
    """
    return {
        "inpaint": model_groups("watermark_removal"),
        "upscale": model_groups("upscale"),
        "cutout": model_groups("cutout"),
    }


@router.get("/defaults")
async def get_defaults():
    """
    返回所有模式的默认模型配置。

    返回格式：
    ```json
    {
      "watermark_removal": "wm_lama",
      "background_replace": "birefnet",
      ...
    }
    ```
    """
    from core.model_registry import MODE_GROUPS
    
    result = {}
    for g in MODE_GROUPS:
        default = g.get("default_model")
        if default:
            result[g["id"]] = default
    
    return result
