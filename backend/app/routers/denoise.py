"""图片降噪路由：restoration（spandrel）或 realesrgan（outscale 由 YAML 锁定）。"""
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

_DENOISE_PROVIDERS = frozenset({"restoration", "realesrgan"})


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
            "suitable_for": m.get("suitable_for", ""),
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
    provider = cfg.get("provider")
    if provider not in _DENOISE_PROVIDERS or "denoise" not in cfg.get("tags", []):
        raise HTTPException(
            status_code=400,
            detail=f"模型 {model_id} 不属于 denoise 模式",
        )
    return cfg


def _match_input_size(result: np.ndarray, src: np.ndarray) -> np.ndarray:
    h, w = src.shape[:2]
    rh, rw = result.shape[:2]
    if rh == h and rw == w:
        return result
    if rh >= h and rw >= w:
        return result[:h, :w]
    return cv2.resize(result, (w, h), interpolation=cv2.INTER_LINEAR)


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
    model: str = "denoise-scunet-psnr"
    device: str = "mps"
    tile: int | None = None
    output_path: str | None = None


class BatchDenoiseRequest(BaseModel):
    images: list[str]
    model: str = "denoise-scunet-psnr"
    device: str = "mps"
    tile: int | None = None
    output_dir: str | None = None
    is_file_path: bool = False


class DenoiseBackend:
    """统一 load / run / close，供单张与批量共用。"""

    def __init__(self, upscaler=None, executor=None, tile: int = 0):
        self._upscaler = upscaler
        self._executor = executor
        self._tile = tile

    def load(self) -> None:
        if self._upscaler is not None:
            if self._tile > 0:
                self._upscaler._current_tile = self._tile
            self._upscaler._ensure_model_loaded()
        elif self._executor is not None:
            self._executor.load_model()

    def run(self, image: np.ndarray) -> np.ndarray:
        if self._upscaler is not None:
            return _match_input_size(self._upscaler.upscale(image), image)
        return _match_input_size(
            self._executor.execute(image, tile=self._tile), image
        )

    def close(self) -> None:
        if self._executor is not None:
            self._executor.unload_model()
        self._upscaler = None
        self._executor = None


def create_denoise_backend(model: str, device: str, tile: int) -> DenoiseBackend:
    from core.model_registry import get_model
    from core.restoration_executor import RestorationExecutor
    from core.upscaler import Upscaler

    cfg = get_model(model)
    if cfg.get("provider") == "realesrgan":
        return DenoiseBackend(
            upscaler=Upscaler(model_name=model, device=device, tile=tile),
            tile=tile,
        )
    return DenoiseBackend(
        executor=RestorationExecutor(cfg, device),
        tile=0,
    )


def _run_denoise(image: np.ndarray, model: str, device: str, tile: int) -> np.ndarray:
    backend = create_denoise_backend(model, device, tile)
    try:
        return backend.run(image)
    finally:
        backend.close()
        if device == "cuda":
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()


@router.post("/denoise/batch")
async def denoise_image_batch(req: BatchDenoiseRequest):
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
    shared = create_denoise_backend(req.model, req.device, tile)
    if tile > 0:
        try:
            shared.load()
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

        def _process(backend, img):
            return backend.run(img)

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

    shared.close()
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
