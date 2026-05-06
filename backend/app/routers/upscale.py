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


@router.get("/models/upscale")
async def get_upscale_models():
    """
    返回所有超分辨率模型（来自 models.yaml），按 display_group 分组。

    响应格式与前端 useModelStore.ts 对齐：
    {
      "groups": [
        {
          "label": "4x 放大",
          "models": [{"id": "RealESRGAN_x4plus", "name": "4x 通用照片（推荐）", "description": "...", "scale": 4}]
        }
      ]
    }
    """
    from core.model_registry import get_models_for_mode

    # 从注册表读取 upscale 模式下的所有模型
    models = get_models_for_mode("upscale")

    # 按 display_group 顺序分组（保留 YAML 中的模型顺序）
    groups: dict[str, list] = {}
    for m in models:
        group_label = m.get("display_group", "其他")
        if group_label not in groups:
            groups[group_label] = []
        groups[group_label].append({
            "id":          m["id"],
            "name":        m.get("name", m["id"]),
            "description": m.get("description", ""),
            "scale":       m.get("scale", 4),
            "badge":       m.get("badge", ""),
        })

    return {
        "groups": [
            {"label": label, "models": model_list}
            for label, model_list in groups.items()
        ]
    }

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
