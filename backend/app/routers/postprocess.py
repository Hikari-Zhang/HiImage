"""
后处理路由

提供：
  GET  /api/postprocess/methods     列出可用的后处理方法
  POST /api/postprocess/fix         对已有图像做后处理修复
  POST /api/pipeline/run            一次性执行完整 inpaint→postprocess→upscale 流程
"""
import asyncio
from typing import List, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services.image_codec import decode_image, decode_mask, encode_image, check_image_size
from app.services.image_codec import validate_matching_dimensions
from app.services.upscale_budget import enforce_upscale_budget
from app.services import image_processing
from app.services.progress import make_progress_callback
from app.shared_executor import inference_admission, inference_scheduler
from app.task_id import TaskId
from app.websocket.progress import progress_manager
from app.logging_manager import log_manager
from app.config import get
from core.model_registry import get_default_model
from core.utils import resolve_default_device

from app.task_route import TaskRoute

router = APIRouter(route_class=TaskRoute)


# ──────────────────────────────────────────────────────────────
# Request / Response Models
# ──────────────────────────────────────────────────────────────

class PostprocessRequest(BaseModel):
    """对已有图像做独立后处理（不含 inpainting 步骤）"""
    original_image: str         # 原始图像 base64（Poisson / LaMa精修 需要）
    inpainted_image: str        # inpainting 输出图像 base64
    mask: str                   # 水印掩码 base64
    method: str = "poisson"     # poisson / gfpgan / lama_refine
    device: str = Field(default_factory=lambda: resolve_default_device(get("inpaint.default_device")))
    task_id: TaskId


class PipelineRequest(BaseModel):
    """完整 Pipeline：inpaint → postprocess → upscale"""
    image: str                        # 原始图像 base64
    rois: Optional[List[List[int]]] = None   # [[x1,y1,x2,y2],...]
    mask: Optional[str] = None        # 或直接传 mask base64

    # Inpaint 参数
    inpaint_model: str = Field(default_factory=lambda: get_default_model("watermark_removal"))
    device: str = Field(default_factory=lambda: resolve_default_device(get("inpaint.default_device")))
    dilation: int = 10
    disable_nsfw: bool = False

    # 后处理参数
    postprocess_method: str = "none"  # none / poisson / gfpgan / lama_refine
    postprocess_enabled: bool = False

    # 超分参数
    upscale_enabled: bool = False
    upscale_model: str = "RealESRGAN_x4plus"

    task_id: TaskId


# ──────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────

@router.get("/postprocess/methods")
async def list_postprocess_methods():
    """列出所有可用的后处理方法"""
    from core.background_fixer import METHOD_GROUPS
    result = []
    for group_label, methods in METHOD_GROUPS:
        items = [
            {"id": mid, "name": name, "description": desc}
            for mid, name, desc in methods
        ]
        result.append({"group": group_label, "methods": items})
    return {"groups": result}


@router.post("/postprocess/fix", dependencies=[Depends(inference_admission)])
async def postprocess_fix(req: PostprocessRequest):
    """对已有 inpainting 结果做后处理修复"""
    for img_b64 in (req.original_image, req.inpainted_image):
        if (err := check_image_size(img_b64)):
            return err

    original = decode_image(req.original_image)
    inpainted = decode_image(req.inpainted_image)
    mask = decode_mask(req.mask)
    validate_matching_dimensions(original, inpainted, "处理结果")
    validate_matching_dimensions(original, mask)

    log_manager.info(
        f"开始后处理修复: method={req.method}, device={req.device}",
        source="postprocess",
    )
    await progress_manager.send_progress(10, f"正在进行后处理（{req.method}）...", task_id=req.task_id)

    try:
        result = await inference_scheduler.run_blocking(
            image_processing.postprocess_image, original, inpainted, mask, req.method, req.device,
        )
        await progress_manager.send_complete("后处理完成", task_id=req.task_id)
        log_manager.info("后处理修复完成", source="postprocess")
        return {"image": encode_image(result)}
    except Exception as e:
        err_str = str(e)
        await progress_manager.send_error(err_str, task_id=req.task_id)
        log_manager.error(f"后处理修复失败: {err_str}", source="postprocess")
        first_line = err_str.split('\n')[0]
        return JSONResponse(status_code=422, content={"detail": err_str, "message": first_line})


@router.post("/pipeline/run", dependencies=[Depends(inference_admission)])
async def run_pipeline(req: PipelineRequest):
    """完整处理 Pipeline：inpaint → postprocess → upscale"""
    if (err := check_image_size(req.image)):
        return err

    original = decode_image(req.image)
    mask_arr = decode_mask(req.mask) if req.mask else None
    if mask_arr is not None:
        validate_matching_dimensions(original, mask_arr)
    if req.upscale_enabled:
        enforce_upscale_budget(original, req.upscale_model)

    log_manager.info(
        f"开始 Pipeline: inpaint={req.inpaint_model}, "
        f"postprocess={req.postprocess_method if req.postprocess_enabled else 'none'}, "
        f"upscale={'on' if req.upscale_enabled else 'off'}",
        source="pipeline",
    )
    await progress_manager.send_progress(5, "正在初始化处理流程...", task_id=req.task_id)

    try:
        result = await inference_scheduler.run_blocking(
            image_processing.pipeline_image, original, mask_arr, req.rois,
            req.model_dump(exclude={"image", "mask", "rois", "task_id"}),
            progress_callback=make_progress_callback(req.task_id, asyncio.get_running_loop(), source="pipeline"),
        )
        h, w = result.shape[:2]
        await progress_manager.send_complete("Pipeline 处理完成", task_id=req.task_id)
        log_manager.info(f"Pipeline 完成: output={w}x{h}", source="pipeline")
        return {
            "image": encode_image(result),
            "width": w,
            "height": h,
        }
    except Exception as e:
        err_str = str(e)
        await progress_manager.send_error(err_str, task_id=req.task_id)
        log_manager.error(f"Pipeline 失败: {err_str}", source="pipeline")
        first_line = err_str.split('\n')[0]
        return JSONResponse(status_code=422, content={"detail": err_str, "message": first_line})
