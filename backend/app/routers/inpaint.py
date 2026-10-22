"""
水印去除路由 - 检测 & 修复
"""
import asyncio
from typing import List

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services.image_codec import (decode_image, decode_mask, encode_image, check_image_size,
                                      validate_matching_dimensions)
from app.services.model_readiness import ensure_model_ready_with_progress
from app.services.progress import make_progress_callback
from app.services import image_processing
from app.services.model_catalog import model_groups
from app.config import get
from app.shared_executor import inference_admission, inference_scheduler
from app.task_id import TaskId
from app.websocket.progress import progress_manager
from app.logging_manager import log_manager
from core.model_registry import get_default_model
from core.utils import resolve_default_device

from app.task_route import TaskRoute

router = APIRouter(route_class=TaskRoute)


# ============ Request/Response Models ============


class DetectRequest(BaseModel):
    image: str  # Base64 encoded PNG/JPG
    sensitivity: float = 0.5
    task_id: TaskId


class InpaintRequest(BaseModel):
    image: str  # Base64 encoded
    rois: List[List[int]]  # [[x1, y1, x2, y2], ...]
    model: str = Field(default_factory=lambda: get_default_model("watermark_removal"))
    device: str = Field(default_factory=lambda: resolve_default_device(get("inpaint.default_device")))
    dilation: int = 10
    disable_nsfw: bool = False
    task_id: TaskId


class InpaintWithMaskRequest(BaseModel):
    image: str  # Base64 encoded
    mask: str  # Base64 encoded mask
    model: str = Field(default_factory=lambda: get_default_model("watermark_removal"))
    device: str = Field(default_factory=lambda: resolve_default_device(get("inpaint.default_device")))
    dilation: int = 10
    disable_nsfw: bool = False
    task_id: TaskId


# ============ Endpoints ============


@router.get("/models/inpaint")
async def get_inpaint_models():
    """
    返回所有去水印模型（来自 models.yaml），按 display_group 分组。

    响应格式与前端 useModelStore.ts 对齐：
    {
      "groups": [
        {
          "label": "快速本地模型",
          "models": [{"id": "wm_lama", "name": "LaMa", "description": "..."}]
        }
      ]
    }
    """
    return model_groups("watermark_removal")


@router.post("/detect", dependencies=[Depends(inference_admission)])
async def detect_watermark(req: DetectRequest):
    """自动检测水印区域"""
    if (err := check_image_size(req.image)):
        return err

    image = decode_image(req.image)
    log_manager.info(f"开始水印检测 (sensitivity={req.sensitivity})", source="inpaint")

    regions = await inference_scheduler.run_blocking(
        image_processing.detect_watermark, image, req.sensitivity,
    )

    log_manager.info(f"水印检测完成，发现 {len(regions)} 个区域", source="inpaint")
    return {
        "regions": [[int(x1), int(y1), int(x2), int(y2)] for x1, y1, x2, y2 in regions]
    }


@router.post("/inpaint", dependencies=[Depends(inference_admission)])
async def inpaint_with_rois(req: InpaintRequest):
    """使用 ROI 列表去除水印"""
    if (err := check_image_size(req.image)):
        return err

    image = decode_image(req.image)
    log_manager.info(
        f"开始水印去除: model={req.model}, device={req.device}, "
        f"rois={len(req.rois)}, dilation={req.dilation}",
        source="inpaint",
    )

    await progress_manager.send_progress(5, "正在检查模型...", task_id=req.task_id)
    success, error_msg = await ensure_model_ready_with_progress(req.model, "水印去除", req.task_id)
    if not success:
        await progress_manager.send_error(error_msg, task_id=req.task_id)
        log_manager.error(f"水印去除失败（模型未就绪）: {error_msg}", source="inpaint")
        return JSONResponse(
            status_code=422,
            content={"detail": error_msg, "message": error_msg}
        )

    await progress_manager.send_progress(10, "正在初始化模型...", task_id=req.task_id)

    loop = asyncio.get_running_loop()

    await progress_manager.send_progress(20, "正在处理...", task_id=req.task_id)

    try:
        result = await inference_scheduler.run_blocking(
            image_processing.inpaint_image, image, req.model, req.device,
            rois=req.rois, progress_callback=make_progress_callback(req.task_id, loop),
        )
        await progress_manager.send_complete(task_id=req.task_id)
        log_manager.info("水印去除完成", source="inpaint")
        return {"image": encode_image(result)}
    except Exception as e:
        err_str = str(e)
        await progress_manager.send_error(err_str, task_id=req.task_id)
        log_manager.error(f"水印去除失败: {err_str}", source="inpaint")
        first_line = err_str.split('\n')[0]
        return JSONResponse(status_code=422, content={"detail": err_str, "message": first_line})


@router.post("/inpaint/with-mask", dependencies=[Depends(inference_admission)])
async def inpaint_with_mask(req: InpaintWithMaskRequest):
    """使用自定义掩码去除水印"""
    if (err := check_image_size(req.image)):
        return err

    image = decode_image(req.image)
    mask = decode_mask(req.mask)
    validate_matching_dimensions(image, mask)
    log_manager.info(
        f"开始掩码修复: model={req.model}, device={req.device}",
        source="inpaint",
    )

    await progress_manager.send_progress(5, "正在检查模型...", task_id=req.task_id)
    success, error_msg = await ensure_model_ready_with_progress(req.model, "掩码修复", req.task_id)
    if not success:
        await progress_manager.send_error(error_msg, task_id=req.task_id)
        log_manager.error(f"掩码修复失败（模型未就绪）: {error_msg}", source="inpaint")
        return JSONResponse(
            status_code=422,
            content={"detail": error_msg, "message": error_msg}
        )

    await progress_manager.send_progress(10, "正在初始化模型...", task_id=req.task_id)

    loop = asyncio.get_running_loop()

    await progress_manager.send_progress(20, "正在处理...", task_id=req.task_id)

    try:
        result = await inference_scheduler.run_blocking(
            image_processing.inpaint_image, image, req.model, req.device,
            mask=mask, progress_callback=make_progress_callback(req.task_id, loop),
        )
        await progress_manager.send_complete(task_id=req.task_id)
        log_manager.info("掩码修复完成", source="inpaint")
        return {"image": encode_image(result)}
    except Exception as e:
        err_str = str(e)
        await progress_manager.send_error(err_str, task_id=req.task_id)
        log_manager.error(f"掩码修复失败: {err_str}", source="inpaint")
        first_line = err_str.split('\n')[0]
        return JSONResponse(status_code=422, content={"detail": err_str, "message": first_line})
