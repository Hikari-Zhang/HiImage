"""
超分辨率路由
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter
from pydantic import BaseModel

from app.routers.inpaint import decode_image, encode_image
from app.websocket.progress import progress_manager
from app.logging_manager import log_manager

router = APIRouter()
executor = ThreadPoolExecutor(max_workers=1)


class UpscaleRequest(BaseModel):
    image: str  # Base64 encoded
    model: str = "RealESRGAN_x4plus"
    device: str = "mps"


@router.post("/upscale")
async def upscale_image(req: UpscaleRequest):
    """超分辨率处理"""
    from core.upscaler import Upscaler

    image = decode_image(req.image)
    h_in, w_in = image.shape[:2]
    log_manager.info(
        f"开始超分辨率: model={req.model}, device={req.device}, input={w_in}x{h_in}",
        source="upscale",
    )

    await progress_manager.send_progress(10, "正在加载超分辨率模型...")

    def _process():
        upscaler = Upscaler(model_name=req.model, device=req.device)
        return upscaler.upscale(image)

    loop = asyncio.get_event_loop()
    await progress_manager.send_progress(30, "正在处理超分辨率...")

    try:
        result = await loop.run_in_executor(executor, _process)
        await progress_manager.send_complete("超分辨率处理完成")

        h, w = result.shape[:2]
        log_manager.info(f"超分辨率完成: output={w}x{h}", source="upscale")
        return {
            "image": encode_image(result),
            "width": w,
            "height": h,
        }
    except Exception as e:
        await progress_manager.send_error(str(e))
        log_manager.error(f"超分辨率失败: {str(e)}", source="upscale")
        raise
