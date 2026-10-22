"""
抠图路由

提供：
  GET  /api/cutout/models       获取抠图模型列表
  POST /api/cutout              执行抠图（返回 RGBA 结果 + alpha mask）
  POST /api/cutout/refine       遮罩精细化（羽化/膨胀/腐蚀/画笔编辑）
  POST /api/cutout/composite    合成到自定义背景色
"""
from typing import Optional

import anyio
import numpy as np
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services.image_codec import (decode_image, encode_image, check_image_size, decode_mask,
                                      decode_rgba_image, encode_rgba_image, encode_mask,
                                      validate_matching_dimensions)
from app.services.model_readiness import ensure_model_ready_with_progress
from app.services import image_processing
from app.services.model_catalog import model_groups
from app.shared_executor import inference_admission, inference_scheduler
from app.task_id import TaskId
from app.websocket.progress import progress_manager
from app.logging_manager import log_manager
from app.config import get
from core.utils import resolve_default_device

from app.task_route import TaskRoute

router = APIRouter(route_class=TaskRoute)


# ──────────────────────────────────────────────────────────────
# Request / Response Models
# ──────────────────────────────────────────────────────────────

class CutoutRequest(BaseModel):
    """抠图请求"""
    image: str              # Base64 原图
    model: str = "birefnet" # 模型 ID
    device: str = Field(default_factory=lambda: resolve_default_device(get("inpaint.default_device")))     # 推理设备
    task_id: TaskId


class RefineRequest(BaseModel):
    """遮罩精细化请求"""
    mask: str                           # Base64 alpha mask (从上次抠图结果或前端编辑后)
    original_image: str                 # Base64 原图 (用于合成)
    feather: int = 0                    # 羽化像素 0-30
    dilate: int = 0                     # 膨胀(正)/腐蚀(负)像素 -20~20
    brush_add: Optional[str] = None     # Base64 画笔添加区域(白=添加前景)
    brush_remove: Optional[str] = None  # Base64 画笔擦除区域(白=擦除前景)
    task_id: TaskId


class CompositeRequest(BaseModel):
    """合成到自定义背景色请求"""
    image: str          # Base64 RGBA 结果图
    background_color: str = "#FFFFFF"  # 十六进制背景色
    task_id: TaskId


# ──────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────

@router.get("/cutout/models")
async def get_cutout_models():
    """
    获取抠图模型列表（来自 models.yaml），按 display_group 分组。

    响应格式：
    {
      "groups": [
        {
          "label": "通用抠图",
          "models": [{"id": "birefnet", "name": "BiRefNet-General", ...}]
        }
      ]
    }
    """
    return model_groups("cutout")


@router.post("/cutout", dependencies=[Depends(inference_admission)])
async def run_cutout(req: CutoutRequest):
    """执行抠图，返回 RGBA 结果图 + alpha mask"""
    if (err := check_image_size(req.image)):
        return err

    source = decode_image(req.image)

    log_manager.info(
        f"开始抠图: model={req.model}, device={req.device}, "
        f"image_size={source.shape[1]}x{source.shape[0]}",
        source="cutout",
    )

    # 检查模型是否就绪
    await progress_manager.send_progress(5, "正在检查模型...", task_id=req.task_id)
    success, error_msg = await ensure_model_ready_with_progress(req.model, "抠图", req.task_id, source="cutout")
    if not success:
        await progress_manager.send_error(error_msg, task_id=req.task_id)
        return JSONResponse(
            status_code=422,
            content={"detail": error_msg, "message": error_msg}
        )

    await progress_manager.send_progress(10, "正在初始化抠图模型...", task_id=req.task_id)

    await progress_manager.send_progress(20, "正在抠图处理...", task_id=req.task_id)

    try:
        result_rgba = await inference_scheduler.run_blocking(
            image_processing.cutout_image, source, req.model, req.device,
        )
        h, w = result_rgba.shape[:2]

        # 提取 alpha mask
        if result_rgba.shape[2] == 4:
            alpha_mask = result_rgba[:, :, 3]
        else:
            # 如果没有 alpha 通道，创建全白 mask
            alpha_mask = np.full((h, w), 255, dtype=np.uint8)

        await progress_manager.send_complete("抠图完成", task_id=req.task_id)
        log_manager.info(f"抠图完成: output={w}x{h}", source="cutout")

        return {
            "image": encode_rgba_image(result_rgba),
            "mask": encode_mask(alpha_mask),
            "width": w,
            "height": h,
        }
    except Exception as e:
        err_str = str(e)
        await progress_manager.send_error(err_str, task_id=req.task_id)
        log_manager.error(f"抠图失败: {err_str}", source="cutout")
        first_line = err_str.split("\n")[0]
        return JSONResponse(
            status_code=422,
            content={"detail": err_str, "message": first_line},
        )


@router.post("/cutout/refine")
async def refine_mask(req: RefineRequest):
    """
    遮罩精细化：羽化、膨胀/腐蚀、画笔编辑。

    纯 OpenCV 处理，无需 GPU。
    """
    if (err := check_image_size(req.original_image)):
        return err

    mask = decode_mask(req.mask)
    original = decode_image(req.original_image)
    validate_matching_dimensions(original, mask)
    brush_add = decode_mask(req.brush_add) if req.brush_add is not None else None
    brush_remove = decode_mask(req.brush_remove) if req.brush_remove is not None else None
    for brush in (brush_add, brush_remove):
        if brush is not None:
            validate_matching_dimensions(original, brush, "画笔遮罩")

    log_manager.info(
        f"遮罩精修: feather={req.feather}, dilate={req.dilate}, "
        f"has_brush_add={req.brush_add is not None}, "
        f"has_brush_remove={req.brush_remove is not None}",
        source="cutout",
    )

    try:
        result_rgba, refined_mask = await anyio.to_thread.run_sync(
            image_processing.refine_cutout, mask, original, brush_add, brush_remove, req.feather, req.dilate,
        )
        h, w = result_rgba.shape[:2]

        log_manager.info(f"遮罩精修完成: output={w}x{h}", source="cutout")

        return {
            "image": encode_rgba_image(result_rgba),
            "mask": encode_mask(refined_mask),
            "width": w,
            "height": h,
        }
    except Exception as e:
        err_str = str(e)
        log_manager.error(f"遮罩精修失败: {err_str}", source="cutout")
        return JSONResponse(
            status_code=422,
            content={"detail": err_str, "message": err_str.split("\n")[0]},
        )


@router.post("/cutout/composite")
async def composite_cutout(req: CompositeRequest):
    """
    将抠图结果合成到自定义背景色上。

    将 RGBA 结果图与纯色背景合成，返回 RGB 结果。
    """
    image_rgba = decode_rgba_image(req.image)
    try:
        result = await anyio.to_thread.run_sync(
            image_processing.composite_cutout, image_rgba, req.background_color,
        )
        h, w = result.shape[:2]

        return {
            "image": encode_image(result),
            "width": w,
            "height": h,
        }
    except Exception as e:
        err_str = str(e)
        log_manager.error(f"合成失败: {err_str}", source="cutout")
        return JSONResponse(
            status_code=422,
            content={"detail": err_str, "message": err_str.split("\n")[0]},
        )
