"""
超分辨率路由
"""
import asyncio
import os

import cv2
import numpy as np
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
            "outscale":    m.get("outscale", m.get("scale", 4)),
            "badge":       m.get("badge", ""),
            # 是否支持自定义输出倍率（前端据此显示/隐藏倍率选项）
            "supports_custom_outscale": m.get("supports_custom_outscale", False),
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
    outscale: int | None = None  # 前端覆盖倍率；None 则使用模型默认值
    tile: int | None = None     # 分块大小；0 = 自动计算；None = 使用默认值


class BatchUpscaleRequest(BaseModel):
    """批量超分辨率请求"""
    images: list[str]  # Base64 编码图片列表，或文件路径列表（当 is_file_path=True 时）
    model: str = "RealESRGAN_x4plus"
    device: str = "mps"
    outscale: int | None = None
    tile: int | None = None
    output_dir: str | None = None  # 非空时直接保存文件，返回文件路径而非 base64
    is_file_path: bool = False  # True 时 images 为文件路径列表


@router.post("/upscale/batch")
async def upscale_image_batch(req: BatchUpscaleRequest):
    """
    批量超分辨率处理

    - images: 图片列表（Base64 或文件路径）
    - output_dir: 若指定，结果直接保存到该目录，返回文件路径；否则返回 Base64
    - is_file_path: True 时 images 为文件路径列表（Electron 模式优化）
    """
    from core.upscaler import Upscaler

    total = len(req.images)
    if total == 0:
        return {"results": [], "summary": {"total": 0, "success": 0, "failed": 0}}

    log_manager.info(
        f"开始批量超分辨率: 共 {total} 张, model={req.model}, device={req.device}",
        source="upscale_batch",
    )

    # 输出目录模式：提前创建目录
    if req.output_dir:
        os.makedirs(req.output_dir, exist_ok=True)

    results = []
    success_count = 0
    failed_count = 0

    for idx, img_data in enumerate(req.images):
        current = idx + 1
        file_name = f"image_{current}.png"  # 默认值
        # 读取图片
        try:
            if req.is_file_path:
                # Electron 模式：直接读取文件
                file_path = img_data
                file_name = os.path.basename(file_path)
                import cv2
                img_bgr = cv2.imread(file_path)
                if img_bgr is None:
                    raise ValueError(f"无法读取图片: {file_path}")
                image = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
            else:
                # Web 模式：Base64 解码
                image = decode_image(img_data)
        except Exception as e:
            failed_count += 1
            results.append({
                "index": idx,
                "status": "error",
                "error": f"读取图片失败: {str(e)}",
                "fileName": file_name,
            })
            continue

        h_in, w_in = image.shape[:2]
        msg = f"正在处理第 {current}/{total} 张: {file_name} ({w_in}x{h_in})"
        progress = int((idx / total) * 100)
        await progress_manager.send_progress(progress, msg)
        log_manager.info(msg, source="upscale_batch")

        # 处理
        def _process():
            tile = req.tile if req.tile is not None else 0
            upscaler = Upscaler(
                model_name=req.model,
                device=req.device,
                outscale=req.outscale,
                tile=tile,
            )
            return upscaler.upscale(image)

        loop = asyncio.get_event_loop()
        try:
            result_rgb = await loop.run_in_executor(executor, _process)
            h_out, w_out = result_rgb.shape[:2]

            # 保存或编码结果
            if req.output_dir:
                os.makedirs(req.output_dir, exist_ok=True)
                base_name = os.path.splitext(file_name)[0]
                out_path = os.path.join(req.output_dir, f"{base_name}_upscaled.png")
                result_bgr = cv2.cvtColor(result_rgb, cv2.COLOR_RGB2BGR)
                cv2.imwrite(out_path, result_bgr)
                results.append({
                    "index": idx,
                    "status": "success",
                    "outputPath": out_path,
                    "width": w_out,
                    "height": h_out,
                    "fileName": file_name,
                })
            else:
                results.append({
                    "index": idx,
                    "status": "success",
                    "image": encode_image(result_rgb),
                    "width": w_out,
                    "height": h_out,
                    "fileName": file_name,
                })
            success_count += 1
        except Exception as e:
            failed_count += 1
            results.append({
                "index": idx,
                "status": "error",
                "error": str(e),
                "fileName": file_name,
            })
            log_manager.error(f"批量超分辨率失败（第 {current}/{total} 张）: {str(e)}", source="upscale_batch")

    # 完成
    await progress_manager.send_progress(100, f"批量处理完成：成功 {success_count} 张，失败 {failed_count} 张")
    log_manager.info(f"批量超分辨率完成: 成功 {success_count}/{total}", source="upscale_batch")
    await progress_manager.send_complete(f"批量处理完成：{success_count} 成功，{failed_count} 失败")

    return {
        "results": results,
        "summary": {
            "total": total,
            "success": success_count,
            "failed": failed_count,
        },
    }


@router.post("/upscale")
async def upscale_image(req: UpscaleRequest):
    """超分辨率处理（单张）"""
    from core.upscaler import Upscaler

    image = decode_image(req.image)
    h_in, w_in = image.shape[:2]
    log_manager.info(
        f"开始超分辨率: model={req.model}, device={req.device}, outscale={req.outscale}, input={w_in}x{h_in}",
        source="upscale",
    )

    await progress_manager.send_progress(10, "正在加载超分辨率模型...")

    def _process():
        # tile: None = 使用默认配置；0 = 自动计算；>0 = 固定 tile 大小
        upscaler = Upscaler(
            model_name=req.model,
            device=req.device,
            outscale=req.outscale,
            tile=req.tile if req.tile is not None else 0,
        )
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
