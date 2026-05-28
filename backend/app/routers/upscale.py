"""
超分辨率路由
"""
import asyncio
import os

import cv2
import numpy as np
from fastapi import APIRouter
from pydantic import BaseModel

from app.routers.inpaint import decode_image, encode_image, check_image_size
from app.shared_executor import executor
from app.websocket.progress import progress_manager
from app.logging_manager import log_manager

router = APIRouter()


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
    """单张超分辨率请求"""
    image: str  # Base64 encoded
    model: str = "RealESRGAN_x4plus"
    device: str = "mps"
    outscale: int | None = None  # 前端覆盖倍率；None 则使用模型默认值
    tile: int | None = None     # 分块大小；0 = 自动计算；None = 使用默认值
    output_path: str | None = None  # 非空时直接保存文件，不返回 base64
    task_id: str = ""  # WebSocket 隔离频道 ID（客户端生成，空=全局广播）


class BatchUpscaleRequest(BaseModel):
    """批量超分辨率请求"""
    images: list[str]  # Base64 编码图片列表，或文件路径列表（当 is_file_path=True 时）
    model: str = "RealESRGAN_x4plus"
    device: str = "mps"
    outscale: int | None = None
    tile: int | None = None
    output_dir: str | None = None  # 非空时直接保存文件，返回文件路径而非 base64
    is_file_path: bool = False  # True 时 images 为文件路径列表
    task_id: str = ""  # WebSocket 隔离频道 ID（客户端生成，空=全局广播）


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

    # ★ 循环外创建 Upscaler 实例，批量复用，避免每张图重复加载模型到显存
    tile = req.tile if req.tile is not None else 0
    shared_upscaler = Upscaler(
        model_name=req.model,
        device=req.device,
        outscale=req.outscale,
        tile=tile,
    )
    # 预加载模型（循环前就加载好，避免每张图都触发加载）
    try:
        shared_upscaler._ensure_model_loaded()
    except Exception as e:
        log_manager.error(f"批量超分模型加载失败: {e}", source="upscale_batch")
        raise RuntimeError(f"模型加载失败: {e}")

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
                img_bgr = cv2.imread(file_path)
                if img_bgr is None:
                    raise ValueError(f"无法读取图片: {file_path}")
                image = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
            else:
                # Web 模式：Base64 解码（先做尺寸预检）
                if check_image_size(img_data):
                    raise ValueError("图像超出大小限制，请压缩后重试")
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
        await progress_manager.send_progress(progress, msg, task_id=req.task_id)
        log_manager.info(msg, source="upscale_batch")

        # 处理：复用 shared_upscaler，不再每张图新建实例
        def _process(upscaler_instance, img):
            return upscaler_instance.upscale(img)

        loop = asyncio.get_running_loop()
        try:
            result_rgb = await loop.run_in_executor(executor, _process, shared_upscaler, image)
            # 释放输入图像（已处理完毕）
            del image

            h_out, w_out = result_rgb.shape[:2]

            # 保存或编码结果
            if req.output_dir:
                base_name = os.path.splitext(file_name)[0]
                out_path = os.path.join(req.output_dir, f"{base_name}_upscaled.png")
                result_bgr = cv2.cvtColor(result_rgb, cv2.COLOR_RGB2BGR)
                cv2.imwrite(out_path, result_bgr)
                # 释放 result
                del result_rgb
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
                # 释放 result
                del result_rgb
            success_count += 1
        except Exception as e:
            failed_count += 1
            results.append({
                "index": idx,
                "status": "error",
                "error": str(e),
                "fileName": file_name,
            })
            # 异常时也释放 image
            if 'image' in locals():
                del image
            log_manager.error(f"批量超分辨率失败（第 {current}/{total} 张）: {str(e)}", source="upscale_batch")

    # 完成
    await progress_manager.send_progress(100, f"批量处理完成：成功 {success_count} 张，失败 {failed_count} 张", task_id=req.task_id)
    log_manager.info(f"批量超分辨率完成: 成功 {success_count}/{total}", source="upscale_batch")
    await progress_manager.send_complete(f"批量处理完成：{success_count} 成功，{failed_count} 失败", task_id=req.task_id)

    # 批量处理完成，释放模型和显存
    del shared_upscaler
    if req.device == 'cuda':
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    log_manager.info(f"批量超分完成，已释放模型显存", source="upscale_batch")

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

    if (err := check_image_size(req.image)):
        return err

    image = decode_image(req.image)
    h_in, w_in = image.shape[:2]
    log_manager.info(
        f"开始超分辨率: model={req.model}, device={req.device}, outscale={req.outscale}, input={w_in}x{h_in}",
        source="upscale",
    )

    await progress_manager.send_progress(10, "正在加载超分辨率模型...", task_id=req.task_id)

    tile = req.tile if req.tile is not None else 0

    def _process(img):
        upscaler = Upscaler(
            model_name=req.model,
            device=req.device,
            outscale=req.outscale,
            tile=tile,
        )
        r = upscaler.upscale(img)
        # 释放 upscaler（释放模型显存）
        del upscaler
        if req.device == 'cuda':
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        return r

    loop = asyncio.get_running_loop()
    await progress_manager.send_progress(30, "正在处理超分辨率...", task_id=req.task_id)

    result = None
    try:
        result = await loop.run_in_executor(executor, _process, image)
        # 释放输入图像（已在子线程中使用完毕）
        del image

        await progress_manager.send_complete("超分辨率处理完成", task_id=req.task_id)

        h, w = result.shape[:2]
        log_manager.info(f"超分辨率完成: output={w}x{h}", source="upscale")

        # output_path 非空时直接保存文件，不返回 base64
        if req.output_path:
            out_dir = os.path.dirname(req.output_path) or "."
            os.makedirs(out_dir, exist_ok=True)
            result_bgr = cv2.cvtColor(result, cv2.COLOR_RGB2BGR)
            cv2.imwrite(req.output_path, result_bgr)
            # 释放 result
            del result
            if req.device == 'cuda':
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            return {
                "outputPath": req.output_path,
                "width": w,
                "height": h,
            }

        # 编码并返回（Web 模式）
        encoded = encode_image(result)
        # 释放 result
        del result
        if req.device == 'cuda':
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        return {
            "image": encoded,
            "width": w,
            "height": h,
        }
    except Exception as e:
        # 异常时也清理
        if image is not None:
            del image
        if result is not None:
            del result
        if req.device == 'cuda':
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        await progress_manager.send_error(str(e), task_id=req.task_id)
        log_manager.error(f"超分辨率失败: {str(e)}", source="upscale")
        raise
