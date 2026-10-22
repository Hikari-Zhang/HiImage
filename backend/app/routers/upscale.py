"""
超分辨率路由
"""
import sys

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.services.image_codec import decode_image, encode_image, check_image_size
from app.services.upscale_budget import enforce_upscale_budget
from app.services.model_catalog import model_groups
from app.services import image_processing
from app.shared_executor import inference_admission, inference_scheduler
from app.task_id import TaskId
from app.websocket.progress import progress_manager
from app.logging_manager import log_manager
from app.config import get
from core.utils import resolve_default_device

from app.task_route import TaskRoute

router = APIRouter(route_class=TaskRoute)


def enforce_output_pixel_budget(image: np.ndarray, model_name: str, outscale: int | None) -> int:
    return enforce_upscale_budget(image, model_name, outscale, max_mb=int(get("image.max_output_mb", 256)))


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
    return model_groups("upscale")


class UpscaleRequest(BaseModel):
    """单张超分辨率请求"""
    model_config = ConfigDict(extra="forbid")

    image: str  # Base64 encoded
    model: str = "RealESRGAN_x4plus"
    device: str = Field(default_factory=lambda: resolve_default_device(get("inpaint.default_device")))
    outscale: int | None = None  # 前端覆盖倍率；None 则使用模型默认值
    tile: int | None = None     # 分块大小；0 = 自动计算；None = 使用默认值
    task_id: TaskId


class BatchUpscaleRequest(BaseModel):
    """批量超分辨率请求"""
    model_config = ConfigDict(extra="forbid")

    images: list[str] = Field(max_length=100)  # Base64 编码图片列表
    model: str = "RealESRGAN_x4plus"
    device: str = Field(default_factory=lambda: resolve_default_device(get("inpaint.default_device")))
    outscale: int | None = None
    tile: int | None = None
    task_id: TaskId


@router.post("/upscale/batch", dependencies=[Depends(inference_admission)])
async def upscale_image_batch(req: BatchUpscaleRequest):
    """
    批量超分辨率处理

    - images: Base64 图片列表
    - results: 每项仅返回 Base64 图片或错误信息
    """
    total = len(req.images)
    if total == 0:
        return {"results": [], "summary": {"total": 0, "success": 0, "failed": 0}}

    def _error_message(error: Exception) -> str:
        if isinstance(error, HTTPException):
            return str(error.detail)
        return str(error)

    def _read_item(img_data: str) -> np.ndarray:
        if check_image_size(img_data):
            raise ValueError("图像超出大小限制，请压缩后重试")
        return decode_image(img_data)

    # 在加载模型和执行任何推理前预检全部图片，避免高压缩大尺寸图片绕过限制。
    results_by_index: list[dict | None] = [None] * total
    valid_indices: list[int] = []
    total_output_bytes = 0
    for idx, img_data in enumerate(req.images):
        file_name = f"image_{idx + 1}.png"
        preflight_image = None
        try:
            preflight_image = _read_item(img_data)
            total_output_bytes += enforce_output_pixel_budget(preflight_image, req.model, req.outscale)
            valid_indices.append(idx)
        except Exception as error:
            results_by_index[idx] = {
                "index": idx,
                "status": "error",
                "error": f"预检失败: {_error_message(error)}",
                "fileName": file_name,
            }
        finally:
            if preflight_image is not None:
                del preflight_image

    if total_output_bytes > int(get("image.max_output_mb", 256)) * 1024 * 1024:
        raise HTTPException(413, "批量预计输出 RGBA 超出总预算，请分批处理")

    failed_count = total - len(valid_indices)
    if not valid_indices:
        return {
            "results": [result for result in results_by_index if result is not None],
            "summary": {"total": total, "success": 0, "failed": failed_count},
        }

    log_manager.info(
        f"开始批量超分辨率: 共 {total} 张, model={req.model}, device={req.device}",
        source="upscale_batch",
    )

    # 循环外在线程执行器内创建并预加载，整个批次复用同一实例。
    tile = req.tile if req.tile is not None else 0
    created_upscalers = []

    def _create_upscaler():
        upscaler = image_processing.create_upscaler(
            model_id=req.model,
            device=req.device,
            outscale=req.outscale,
            tile=tile,
        )
        created_upscalers.append(upscaler)
        return upscaler

    shared_upscaler = None
    try:
        try:
            shared_upscaler = await inference_scheduler.run_blocking(
                _create_upscaler
            )
        finally:
            if shared_upscaler is None and created_upscalers:
                shared_upscaler = created_upscalers[0]
        try:
            await inference_scheduler.run_blocking(
                shared_upscaler._ensure_model_loaded
            )
        except Exception as error:
            log_manager.error(
                f"批量超分模型加载失败: {error}",
                source="upscale_batch",
            )
            raise RuntimeError(f"模型加载失败: {error}") from error

        success_count = 0

        for idx in valid_indices:
            img_data = req.images[idx]
            current = idx + 1
            file_name = f"image_{current}.png"
            image = None
            try:
                image = _read_item(img_data)
                enforce_output_pixel_budget(image, req.model, req.outscale)
            except Exception as error:
                failed_count += 1
                results_by_index[idx] = {
                    "index": idx,
                    "status": "error",
                    "error": f"读取图片失败: {_error_message(error)}",
                    "fileName": file_name,
                }
                if image is not None:
                    image = None
                continue

            h_in, w_in = image.shape[:2]
            msg = f"正在处理第 {current}/{total} 张: {file_name} ({w_in}x{h_in})"
            progress = int((idx / total) * 100)
            await progress_manager.send_progress(
                progress,
                msg,
                task_id=req.task_id,
            )
            log_manager.info(msg, source="upscale_batch")

            def _process(upscaler_instance, img):
                return upscaler_instance.upscale(img)

            try:
                result_rgb = await inference_scheduler.run_blocking(
                    _process,
                    shared_upscaler,
                    image,
                )
                image = None
                h_out, w_out = result_rgb.shape[:2]
                results_by_index[idx] = {
                    "index": idx,
                    "status": "success",
                    "image": encode_image(result_rgb),
                    "width": w_out,
                    "height": h_out,
                    "fileName": file_name,
                }
                del result_rgb
                success_count += 1
            except Exception as error:
                failed_count += 1
                results_by_index[idx] = {
                    "index": idx,
                    "status": "error",
                    "error": str(error),
                    "fileName": file_name,
                }
                if image is not None:
                    image = None
                log_manager.error(
                    f"批量超分辨率失败（第 {current}/{total} 张）: {error}",
                    source="upscale_batch",
                )

        await progress_manager.send_progress(
            100,
            f"批量处理完成：成功 {success_count} 张，失败 {failed_count} 张",
            task_id=req.task_id,
        )
        log_manager.info(
            f"批量超分辨率完成: 成功 {success_count}/{total}",
            source="upscale_batch",
        )
        await progress_manager.send_complete(
            f"批量处理完成：{success_count} 成功，{failed_count} 失败",
            task_id=req.task_id,
        )
        return {
            "results": [
                result for result in results_by_index if result is not None
            ],
            "summary": {
                "total": total,
                "success": success_count,
                "failed": failed_count,
            },
        }
    finally:
        if shared_upscaler is not None:
            active_error = sys.exc_info()[1]
            try:
                await inference_scheduler.run_blocking(shared_upscaler.close)
                log_manager.info(
                    "批量超分完成，已释放模型显存",
                    source="upscale_batch",
                )
            except BaseException as cleanup_error:
                log_manager.error(
                    f"批量超分资源清理失败: {cleanup_error}",
                    source="upscale_batch",
                )
                if active_error is None:
                    raise
            finally:
                shared_upscaler = None
                created_upscalers.clear()


@router.post("/upscale", dependencies=[Depends(inference_admission)])
async def upscale_image(req: UpscaleRequest):
    """超分辨率处理（单张）"""
    if (err := check_image_size(req.image)):
        return err

    image = decode_image(req.image)
    enforce_output_pixel_budget(image, req.model, req.outscale)
    h_in, w_in = image.shape[:2]
    log_manager.info(
        f"开始超分辨率: model={req.model}, device={req.device}, outscale={req.outscale}, input={w_in}x{h_in}",
        source="upscale",
    )

    await progress_manager.send_progress(10, "正在加载超分辨率模型...", task_id=req.task_id)

    tile = req.tile if req.tile is not None else 0

    await progress_manager.send_progress(30, "正在处理超分辨率...", task_id=req.task_id)

    result = None
    try:
        result = await inference_scheduler.run_blocking(
            image_processing.upscale_image, image, req.model, req.device, req.outscale, tile,
        )
        # 释放输入图像（已在子线程中使用完毕）
        image = None

        await progress_manager.send_complete("超分辨率处理完成", task_id=req.task_id)

        h, w = result.shape[:2]
        log_manager.info(f"超分辨率完成: output={w}x{h}", source="upscale")

        encoded = encode_image(result)
        # 释放 result
        result = None
        return {
            "image": encoded,
            "width": w,
            "height": h,
        }
    except Exception as e:
        # 异常时也清理
        if image is not None:
            image = None
        if result is not None:
            result = None
        await progress_manager.send_error(str(e), task_id=req.task_id)
        log_manager.error(f"超分辨率失败: {str(e)}", source="upscale")
        raise
