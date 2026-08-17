"""图片降噪路由：同分辨率 Real-ESRGAN（outscale 由 YAML 锁定为 1）。"""
import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.routers.inpaint import decode_image, encode_image
from app.websocket.progress import progress_manager
from app.logging_manager import log_manager

router = APIRouter()
executor = ThreadPoolExecutor(max_workers=1)


def list_denoise_models() -> dict:
    from core.model_registry import get_models_for_mode

    models = get_models_for_mode("denoise")
    groups: dict[str, list] = {}
    for m in models:
        label = m.get("display_group", "其他")
        if label not in groups:
            groups[label] = []
        groups[label].append({
            "id": m["id"],
            "name": m.get("name", m["id"]),
            "description": m.get("description", ""),
            "badge": m.get("badge", ""),
        })
    return {
        "groups": [
            {"label": label, "models": model_list}
            for label, model_list in groups.items()
        ]
    }


def _require_denoise_model(model_id: str) -> dict:
    from core.model_registry import get_model, get_models_for_mode

    try:
        cfg = get_model(model_id)
    except KeyError:
        allowed = [m["id"] for m in get_models_for_mode("denoise")]
        raise HTTPException(
            status_code=400,
            detail=f"未知降噪模型: {model_id!r}，可选: {allowed}",
        ) from None
    if cfg.get("provider") != "realesrgan" or "denoise" not in cfg.get("tags", []):
        raise HTTPException(
            status_code=400,
            detail=f"模型 {model_id} 不属于 denoise 模式",
        )
    return cfg


def _match_input_size(result: np.ndarray, src: np.ndarray) -> np.ndarray:
    h, w = src.shape[:2]
    if result.shape[0] != h or result.shape[1] != w:
        result = cv2.resize(result, (w, h), interpolation=cv2.INTER_AREA)
    return result


def _denoised_output_path(output_dir: str, file_name: str) -> str:
    base_name = os.path.splitext(file_name)[0]
    output_path = os.path.join(output_dir, f"{base_name}_denoised.png")
    suffix = 2
    while os.path.exists(output_path):
        output_path = os.path.join(
            output_dir, f"{base_name}_denoised_{suffix}.png"
        )
        suffix += 1
    if suffix > 2:
        log_manager.warning(
            f"降噪输出文件名冲突，使用后缀路径: {output_path}",
            source="denoise_batch",
        )
    return output_path


@router.get("/models/denoise")
async def get_denoise_models():
    return list_denoise_models()


class DenoiseRequest(BaseModel):
    image: str
    model: str = "denoise-general"
    device: str = "mps"
    tile: int | None = None
    output_path: str | None = None


class BatchDenoiseRequest(BaseModel):
    images: list[str]
    model: str = "denoise-general"
    device: str = "mps"
    tile: int | None = None
    output_dir: str | None = None
    is_file_path: bool = False


def _run_denoise(image: np.ndarray, model: str, device: str, tile: int) -> np.ndarray:
    from core.upscaler import Upscaler

    upscaler = Upscaler(model_name=model, device=device, tile=tile)
    result = upscaler.upscale(image)
    del upscaler
    if device == "cuda":
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    return _match_input_size(result, image)


@router.post("/denoise/batch")
async def denoise_image_batch(req: BatchDenoiseRequest):
    from core.upscaler import Upscaler

    _require_denoise_model(req.model)
    total = len(req.images)
    if total == 0:
        return {"results": [], "summary": {"total": 0, "success": 0, "failed": 0}}

    log_manager.info(
        f"开始批量降噪: 共 {total} 张, model={req.model}, device={req.device}",
        source="denoise_batch",
    )
    if req.output_dir:
        os.makedirs(req.output_dir, exist_ok=True)

    tile = req.tile if req.tile is not None else 0
    shared = Upscaler(model_name=req.model, device=req.device, tile=tile)
    if tile > 0:
        shared._current_tile = tile
        try:
            shared._ensure_model_loaded()
        except Exception as e:
            log_manager.error(f"批量降噪模型加载失败: {e}", source="denoise_batch")
            raise RuntimeError(f"模型加载失败: {e}") from e

    results = []
    success_count = 0
    failed_count = 0

    for idx, img_data in enumerate(req.images):
        current = idx + 1
        file_name = f"image_{current}.png"
        try:
            if req.is_file_path:
                file_path = img_data
                file_name = os.path.basename(file_path)
                img_bgr = cv2.imread(file_path)
                if img_bgr is None:
                    raise ValueError(f"无法读取图片: {file_path}")
                image = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
            else:
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
        log_manager.info(msg, source="denoise_batch")

        def _process(upscaler_instance, img):
            out = upscaler_instance.upscale(img)
            return _match_input_size(out, img)

        loop = asyncio.get_event_loop()
        try:
            result_rgb = await loop.run_in_executor(executor, _process, shared, image)
            del image
            h_out, w_out = result_rgb.shape[:2]
            if req.output_dir:
                out_path = _denoised_output_path(req.output_dir, file_name)
                result_bgr = cv2.cvtColor(result_rgb, cv2.COLOR_RGB2BGR)
                if not cv2.imwrite(out_path, result_bgr):
                    raise RuntimeError(f"写入降噪图片失败: {out_path}")
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
            if "image" in locals():
                del image
            log_manager.error(
                f"批量降噪失败（第 {current}/{total} 张）: {str(e)}",
                source="denoise_batch",
            )

    await progress_manager.send_progress(
        100, f"批量处理完成：成功 {success_count} 张，失败 {failed_count} 张"
    )
    log_manager.info(f"批量降噪完成: 成功 {success_count}/{total}", source="denoise_batch")
    await progress_manager.send_complete(f"批量处理完成：{success_count} 成功，{failed_count} 失败")

    del shared
    if req.device == "cuda":
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    return {
        "results": results,
        "summary": {"total": total, "success": success_count, "failed": failed_count},
    }


@router.post("/denoise")
async def denoise_image(req: DenoiseRequest):
    _require_denoise_model(req.model)
    image = decode_image(req.image)
    h_in, w_in = image.shape[:2]
    log_manager.info(
        f"开始降噪: model={req.model}, device={req.device}, input={w_in}x{h_in}",
        source="denoise",
    )
    await progress_manager.send_progress(10, "正在加载降噪模型...")
    tile = req.tile if req.tile is not None else 0

    def _process(img):
        return _run_denoise(img, req.model, req.device, tile)

    loop = asyncio.get_event_loop()
    await progress_manager.send_progress(30, "正在降噪...")
    result = None
    try:
        result = await loop.run_in_executor(executor, _process, image)
        h, w = result.shape[:2]
        log_manager.info(f"降噪完成: output={w}x{h}", source="denoise")
        if req.output_path:
            out_dir = os.path.dirname(req.output_path) or "."
            os.makedirs(out_dir, exist_ok=True)
            result_bgr = cv2.cvtColor(result, cv2.COLOR_RGB2BGR)
            if not cv2.imwrite(req.output_path, result_bgr):
                raise RuntimeError(f"写入降噪图片失败: {req.output_path}")
            if req.device == "cuda":
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            await progress_manager.send_complete("降噪处理完成")
            return {"outputPath": req.output_path, "width": w, "height": h}
        encoded = encode_image(result)
        if req.device == "cuda":
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        await progress_manager.send_complete("降噪处理完成")
        return {"image": encoded, "width": w, "height": h}
    except Exception as e:
        if req.device == "cuda":
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        await progress_manager.send_error(str(e))
        log_manager.error(f"降噪失败: {str(e)}", source="denoise")
        raise
