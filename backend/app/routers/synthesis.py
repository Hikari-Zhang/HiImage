"""
智能合成路由

提供：
  GET  /api/synthesis/modes          获取所有合成模式（含描述、模型、参考图需求）
  GET  /api/synthesis/models         获取所有可用模型（带功能标签与描述）
  POST /api/synthesis/run            执行合成处理
"""
import asyncio
from typing import List, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.routers.inpaint import decode_image, encode_image, check_image_size, ensure_model_ready_with_progress
from app.shared_executor import inference_admission, inference_scheduler
from app.task_id import TaskId
from app.websocket.progress import progress_manager
from app.logging_manager import log_manager
from core.utils import make_progress_callback, resolve_default_device
from app.config import get

router = APIRouter()


# ──────────────────────────────────────────────────────────────
# Request / Response Models
# ──────────────────────────────────────────────────────────────

class SynthesisRequest(BaseModel):
    """智能合成处理请求"""
    source_image: str                       # 主图 base64
    reference_image: Optional[str] = None  # 参考图 base64（换背景/换装/换脸/试穿需要）
    rois: Optional[List[List[int]]] = None # [[x1,y1,x2,y2],...]；None = 全图处理
    mode: str = "background_replace"       # 合成模式 ID
    model_id: str = "rmbg"                 # 模型 ID
    device: str = Field(default_factory=lambda: resolve_default_device(get("inpaint.default_device")))
    prompt: str = ""                       # SD 系列模型的文字引导（可选）
    task_id: TaskId


# ──────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────

@router.get("/synthesis/modes")
async def list_synthesis_modes():
    """获取所有合成模式（含功能描述、推荐模型、是否需要参考图）"""
    from core.synthesizer import SYNTHESIS_MODE_GROUPS
    return {"modes": SYNTHESIS_MODE_GROUPS}


@router.get("/synthesis/models")
async def list_synthesis_models():
    """获取所有智能合成模型（带功能标签与适用场景描述）"""
    from core.synthesizer import SYNTHESIS_MODELS
    return {"models": SYNTHESIS_MODELS}


@router.post("/synthesis/run", dependencies=[Depends(inference_admission)])
async def run_synthesis(req: SynthesisRequest):
    """执行智能合成处理"""
    if (err := check_image_size(req.source_image)):
        return err
    if req.reference_image and (err := check_image_size(req.reference_image)):
        return err

    source = decode_image(req.source_image)
    reference = decode_image(req.reference_image) if req.reference_image else None
    rois = [tuple(r) for r in req.rois] if req.rois else None

    log_manager.info(
        f"开始智能合成: mode={req.mode}, model={req.model_id}, device={req.device}, "
        f"rois={len(rois) if rois else 0}, has_reference={reference is not None}",
        source="synthesis",
    )
    
    await progress_manager.send_progress(5, "正在检查模型...", task_id=req.task_id)
    success, error_msg = await ensure_model_ready_with_progress(req.model_id, "智能合成", req.task_id, source="synthesis")
    if not success:
        await progress_manager.send_error(error_msg, task_id=req.task_id)
        log_manager.error(f"智能合成失败（模型未就绪）: {error_msg}", source="synthesis")
        return JSONResponse(
            status_code=422,
            content={"detail": error_msg, "message": error_msg}
        )

    await progress_manager.send_progress(10, f"正在初始化 [{req.mode}] 处理...", task_id=req.task_id)

    loop = asyncio.get_running_loop()

    def _process():
        from core.synthesizer import Synthesizer
        synth = Synthesizer(
            mode=req.mode,
            model_id=req.model_id,
            device=req.device,
            prompt=req.prompt,
            progress_callback=make_progress_callback(req.task_id, loop, source="synthesis"),
        )
        return synth.run(
            source_rgb=source,
            rois=rois,
            reference_rgb=reference,
        )

    await progress_manager.send_progress(20, "正在处理中...", task_id=req.task_id)

    try:
        result = await inference_scheduler.run_blocking(_process)
        h, w = result.shape[:2]
        await progress_manager.send_complete("合成完成", task_id=req.task_id)
        log_manager.info(f"智能合成完成: output={w}x{h}", source="synthesis")
        return {
            "image": encode_image(result),
            "width": w,
            "height": h,
        }
    except Exception as e:
        err_str = str(e)
        await progress_manager.send_error(err_str, task_id=req.task_id)
        log_manager.error(f"智能合成失败: {err_str}", source="synthesis")
        first_line = err_str.split("\n")[0]
        return JSONResponse(
            status_code=422,
            content={"detail": err_str, "message": first_line},
        )
