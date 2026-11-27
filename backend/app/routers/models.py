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
from pathlib import Path
from tempfile import TemporaryFile
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services.model_catalog import model_groups
from core.model_checker import ModelChecker
from core.model_registry import MODELS, MODE_GROUPS, MODEL_BY_ID
from core.constants import ModelStatus as MS, DownloadStatus as DS

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


def _can_open_model_folders(request: Request | None) -> bool:
    return (
        os.name == "nt"
        and request is not None
        and request.client is not None
        and request.client.host in {"127.0.0.1", "::1"}
    )


def _local_storage_request(request: Request | None) -> bool:
    if not request or not request.client:
        return False
    hosts = {"127.0.0.1", "::1", "localhost"}
    origin = request.headers.get("origin")
    try:
        return (request.client.host in hosts and request.url.hostname in hosts
                and (not origin or urlsplit(origin).hostname in hosts)
                and all(ip.strip() in hosts for ip in request.headers.get(
                    "x-forwarded-for", request.client.host).split(",")))
    except ValueError:
        return False


class ModelStorageUpdate(BaseModel):
    directories: list[str]


@router.put("/storage")
async def update_model_storage(data: ModelStorageUpdate, request: Request):
    from app.config import get_all, save
    from core.download_queue import get_download_queue
    from core.model_checker import invalidate_model_cache
    from core.model_resources import ModelResourcesBusy, model_resources
    from core.paths import default_model_directory

    if not _local_storage_request(request):
        raise HTTPException(403, "请在运行 HiImage 的电脑上修改模型存储位置")
    try:
        directories = []
        for value in data.directories:
            if not value.strip():
                continue
            directory = Path(value.strip()).expanduser()
            if not directory.is_absolute():
                raise HTTPException(422, f"请输入后端电脑上的绝对目录路径：{value}")
            directory = directory.resolve()
            if directory.exists() and not directory.is_dir():
                raise HTTPException(422, f"路径不是文件夹：{directory}")
            if directory not in directories:
                directories.append(directory)
        with model_resources.deleting():
            if get_download_queue().list_active():
                raise ModelResourcesBusy("下载队列仍有任务，请在下载结束后修改目录")
            destination = directories[0] if directories else default_model_directory()
            destination.mkdir(parents=True, exist_ok=True)
            with TemporaryFile(dir=destination):
                pass
            settings = get_all()
            settings["storage.model_directories"] = [str(path) for path in directories]
            save(settings)
            invalidate_model_cache()
    except ModelResourcesBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(422, f"无法保存模型存储位置：{exc}") from exc
    return {"directories": settings["storage.model_directories"]}


def _model_folder(file_path: str | None) -> Path | None:
    if not file_path:
        return None
    path = Path(file_path)
    try:
        if path.is_dir():
            return path
        if path.is_file():
            return path.parent
    except OSError:
        pass
    return None


def _model_list_entries():
    check_map = {r.model_id: r for r in ModelChecker().check_all()}

    result = []
    for m in MODELS:
        mid = m["id"]
        r = check_map.get(mid)
        result.append({
            **{k: v for k, v in m.items() if k not in ("supported_params",)},
            "status":  r.status if r else MS.UNKNOWN,
            "message": r.message if r else "",
            "has_local_path": r is not None and _model_folder(r.file_path) is not None,
        })
    return result


@router.get("/list")
async def models_list(request: Request = None):
    """返回注册模型、文件状态和当前客户端可用的目录操作。"""
    from app.config import get
    from core.paths import default_model_directory

    storage = None
    if _local_storage_request(request):
        storage = {"directories": get("storage.model_directories", []), "default_directory": str(default_model_directory())}
    return {
        "storage": storage,
        "models": await asyncio.to_thread(_model_list_entries),
        "mode_groups": MODE_GROUPS,
        "can_open_model_folders": _can_open_model_folders(request),
    }


@router.post("/{model_id}/open-folder")
def open_model_folder(model_id: str, request: Request):
    """在本机 Windows 文件资源管理器中打开模型所在目录。"""
    if not _can_open_model_folders(request):
        raise HTTPException(status_code=403, detail="请在运行 HiImage 后端的 Windows 电脑上打开模型目录")
    if request.headers.get("x-hiimage-action") != "open-folder":
        raise HTTPException(status_code=403, detail="无效的打开目录请求")
    if model_id not in MODEL_BY_ID:
        raise HTTPException(status_code=404, detail=f"未知模型 ID: {model_id!r}")

    result = ModelChecker().check_model(model_id, use_cache=False)
    folder = _model_folder(result.file_path)
    if folder is None:
        raise HTTPException(status_code=404, detail="模型文件或目录不存在")

    try:
        os.startfile(str(folder.absolute()), "explore")
    except OSError as exc:
        logger.exception("打开模型目录失败: %s", model_id)
        raise HTTPException(status_code=500, detail="无法打开模型目录，请检查后端是否运行在 Windows 桌面会话中") from exc
    return {"ok": True}


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

    # 检测哪些模型需要下载
    results = await loop.run_in_executor(None, checker.check_all)
    status_map = {r.model_id: r.status for r in results}

    need_download = [
        m for m in MODELS
        if not m.get("setup_hint") and status_map.get(m["id"], MS.MISSING) != MS.OK
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
    if setup_hint := cfg.get("setup_hint"):
        raise HTTPException(status_code=422, detail=setup_hint)

    from core.download_queue import get_download_queue
    queue = get_download_queue()
    task = queue.submit(model_id)
    logger.info(f"[POST /download/{model_id}] submit 返回: status={task.status!r}")
    return task.to_dict()


@router.delete("/download/{model_id}")
async def cancel_download(model_id: str, download_id: str | None = None):
    """
    取消指定模型的下载任务（queued 或 downloading 状态均可取消）。
    可传 download_id，仅取消匹配的下载实例，避免迟到请求取消新任务。
    """
    from core.download_queue import get_download_queue
    queue = get_download_queue()
    success = queue.cancel(model_id, download_id=download_id)
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
    if queue.get_task(model_id) is None:
        raise HTTPException(status_code=404, detail=f"未找到下载任务: {model_id!r}")

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


class DeleteWeightRequest(BaseModel):
    resource_id: str
    affected_model_ids: list[str]


@router.get("/{model_id}/file-resources")
def model_file_resources(model_id: str):
    from core.model_files import affected_model_ids, deletion_resources
    from core.model_registry import MODEL_BY_ID

    cfg = MODEL_BY_ID.get(model_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="未找到模型")
    resources = []
    try:
        for resource_id, (label, targets) in deletion_resources(cfg).items():
            if any(os.path.lexists(path) for path, _ in targets):
                affected = affected_model_ids(targets, MODEL_BY_ID)
                resources.append({
                    "id": resource_id,
                    "label": label,
                    "affected_models": [{"id": mid, "name": MODEL_BY_ID[mid].get("name", mid)} for mid in affected],
                })
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"resources": resources}


@router.delete("/{model_id}/file-resources")
async def delete_model_file_resource(model_id: str, request: DeleteWeightRequest):
    from core.download_queue import get_download_queue
    from core.model_checker import invalidate_model_cache
    from core.model_files import affected_model_ids, delete_targets, deletion_resources
    from core.model_registry import MODEL_BY_ID
    from core.model_resources import ModelResourcesBusy, model_resources

    cfg = MODEL_BY_ID.get(model_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="未找到模型")
    try:
        with model_resources.deleting():
            if get_download_queue().list_active():
                raise ModelResourcesBusy("当前存在下载或排队任务，请结束后再删除模型文件")
            resource = deletion_resources(cfg).get(request.resource_id)
            if resource is None:
                raise HTTPException(status_code=404, detail="未找到该模型权重")
            _, targets = resource
            affected = affected_model_ids(targets, MODEL_BY_ID)
            if sorted(request.affected_model_ids) != affected:
                raise HTTPException(status_code=409, detail="受影响的模型已变化，请重新确认删除")
            try:
                return {**delete_targets(targets), "affected_model_ids": affected}
            finally:
                invalidate_model_cache()
    except ModelResourcesBusy as exc:
        raise HTTPException(status_code=409, detail={"code": "MODEL_FILES_BUSY", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        logger.exception("删除模型权重失败: %s", model_id)
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
      "cutout": "birefnet",
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
