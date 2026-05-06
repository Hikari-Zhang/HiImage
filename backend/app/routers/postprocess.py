"""
后处理路由

提供：
  GET  /api/postprocess/methods     列出可用的后处理方法
  POST /api/postprocess/fix         对已有图像做后处理修复
  POST /api/pipeline/run            一次性执行完整 inpaint→postprocess→upscale 流程
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

import numpy as np
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.routers.inpaint import decode_image, decode_mask, encode_image
from app.websocket.progress import progress_manager
from app.logging_manager import log_manager
from app.config import get

router = APIRouter()
executor = ThreadPoolExecutor(max_workers=1)


# ──────────────────────────────────────────────────────────────
# Request / Response Models
# ──────────────────────────────────────────────────────────────

class PostprocessRequest(BaseModel):
    """对已有图像做独立后处理（不含 inpainting 步骤）"""
    original_image: str         # 原始图像 base64（Poisson / LaMa精修 需要）
    inpainted_image: str        # inpainting 输出图像 base64
    mask: str                   # 水印掩码 base64
    method: str = "poisson"     # poisson / gfpgan / lama_refine
    device: str = "mps"


class PipelineRequest(BaseModel):
    """完整 Pipeline：inpaint → postprocess → upscale"""
    image: str                        # 原始图像 base64
    rois: Optional[List[List[int]]] = None   # [[x1,y1,x2,y2],...]
    mask: Optional[str] = None        # 或直接传 mask base64

    # Inpaint 参数
    inpaint_model: str = "lama"
    device: str = "mps"
    dilation: int = 10
    disable_nsfw: bool = False

    # 后处理参数
    postprocess_method: str = "none"  # none / poisson / gfpgan / lama_refine
    postprocess_enabled: bool = False

    # 超分参数
    upscale_enabled: bool = False
    upscale_model: str = "RealESRGAN_x4plus"


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


@router.post("/postprocess/fix")
async def postprocess_fix(req: PostprocessRequest):
    """对已有 inpainting 结果做后处理修复"""
    original = decode_image(req.original_image)
    inpainted = decode_image(req.inpainted_image)
    mask = decode_mask(req.mask)

    log_manager.info(
        f"开始后处理修复: method={req.method}, device={req.device}",
        source="postprocess",
    )
    await progress_manager.send_progress(10, f"正在进行后处理（{req.method}）...")

    iopaint_path = get("inpaint.iopaint_path", "iopaint")

    def _process():
        from core.background_fixer import fix_background
        return fix_background(
            original_rgb=original,
            inpainted_rgb=inpainted,
            mask=mask,
            method=req.method,
            device=req.device,
            iopaint_path=iopaint_path,
        )

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(executor, _process)
        await progress_manager.send_complete("后处理完成")
        log_manager.info("后处理修复完成", source="postprocess")
        return {"image": encode_image(result)}
    except Exception as e:
        err_str = str(e)
        await progress_manager.send_error(err_str)
        log_manager.error(f"后处理修复失败: {err_str}", source="postprocess")
        first_line = err_str.split('\n')[0]
        return JSONResponse(status_code=422, content={"detail": err_str, "message": first_line})


@router.post("/pipeline/run")
async def run_pipeline(req: PipelineRequest):
    """完整处理 Pipeline：inpaint → postprocess → upscale"""
    original = decode_image(req.image)
    mask_arr = decode_mask(req.mask) if req.mask else None

    log_manager.info(
        f"开始 Pipeline: inpaint={req.inpaint_model}, "
        f"postprocess={req.postprocess_method if req.postprocess_enabled else 'none'}, "
        f"upscale={'on' if req.upscale_enabled else 'off'}",
        source="pipeline",
    )
    await progress_manager.send_progress(5, "正在初始化处理流程...")

    iopaint_path = get("inpaint.iopaint_path", "iopaint")

    def _process():
        from core.pipeline import Pipeline, PipelineConfig, InpaintStep, PostprocessStep, UpscaleStep

        config = PipelineConfig(
            inpaint=InpaintStep(
                model=req.inpaint_model,
                device=req.device,
                dilation=req.dilation,
                disable_nsfw=req.disable_nsfw,
                iopaint_path=iopaint_path,
            ),
            postprocess=PostprocessStep(
                method=req.postprocess_method,
                device=req.device,
                iopaint_path=iopaint_path,
                enabled=req.postprocess_enabled,
            ),
            upscale=UpscaleStep(
                model=req.upscale_model,
                device=req.device,
                enabled=req.upscale_enabled,
            ),
        )

        def _cb(step_name: str, pct: int):
            # 进度回调（同步，仅打印，WebSocket 推送在异步层处理）
            print(f"[Pipeline] {step_name}: {pct}%")

        pipeline = Pipeline(config)
        rois = [tuple(r) for r in req.rois] if req.rois else None
        return pipeline.run(
            original_rgb=original,
            rois=rois,
            mask=mask_arr,
            progress_callback=_cb,
        )

    loop = asyncio.get_event_loop()
    await progress_manager.send_progress(15, "正在处理...")

    try:
        result = await loop.run_in_executor(executor, _process)
        h, w = result.shape[:2]
        await progress_manager.send_complete("Pipeline 处理完成")
        log_manager.info(f"Pipeline 完成: output={w}x{h}", source="pipeline")
        return {
            "image": encode_image(result),
            "width": w,
            "height": h,
        }
    except Exception as e:
        err_str = str(e)
        await progress_manager.send_error(err_str)
        log_manager.error(f"Pipeline 失败: {err_str}", source="pipeline")
        first_line = err_str.split('\n')[0]
        return JSONResponse(status_code=422, content={"detail": err_str, "message": first_line})
