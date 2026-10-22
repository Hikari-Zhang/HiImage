"""阻塞的模型调用与处理链编排；由路由在共享推理线程中调用。

本模块接收图像数组、参数和进度回调，不读取 HTTP 请求、不发送 WebSocket。
"""
from typing import Callable

import numpy as np
import cv2

from core.model_executor import ModelExecutorFactory
from core.model_registry import get_model
from core.model_session import model_session

ProgressCallback = Callable[[int, str], None]


def detect_watermark(image: np.ndarray, sensitivity: float):
    from core.watermark_detector import WatermarkDetector
    return WatermarkDetector(sensitivity=sensitivity).detect(image)


def inpaint_image(image: np.ndarray, model_id: str, device: str, *,
                  rois=None, mask=None, progress_callback: ProgressCallback | None = None):
    executor = ModelExecutorFactory.create_executor(get_model(model_id), device)
    with model_session(executor):
        params = {"progress_callback": progress_callback}
        if mask is not None:
            params["mask"] = mask
        elif executor.supports_mask():
            params["rois"] = rois
        return executor.execute(image, **params)


def cutout_image(image: np.ndarray, model_id: str, device: str):
    executor = ModelExecutorFactory.create_executor(get_model(model_id), device)
    try:
        with model_session(executor):
            return executor.execute(image)
    finally:
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


def create_upscaler(model_id: str, device: str, outscale=None, tile=0):
    from core.upscaler import Upscaler
    return Upscaler(model_name=model_id, device=device, outscale=outscale, tile=tile)


def upscale_image(image: np.ndarray, model_id: str, device: str, outscale=None, tile=0):
    with model_session(create_upscaler(model_id, device, outscale, tile), "close") as model:
        return model.upscale(image)


def refine_cutout(mask: np.ndarray, original: np.ndarray, brush_add, brush_remove,
                   feather: int, dilate: int):
    if brush_add is not None:
        mask[brush_add > 128] = 255
    if brush_remove is not None:
        mask[brush_remove > 128] = 0
    if dilate:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (abs(dilate) * 2 + 1,) * 2)
        mask = cv2.dilate(mask, kernel) if dilate > 0 else cv2.erode(mask, kernel)
    if feather > 0:
        mask = cv2.GaussianBlur(mask, (feather * 2 + 1,) * 2, 0)
    return np.dstack([original, mask]), mask


def composite_cutout(image_rgba: np.ndarray, background_color: str):
    color = background_color.lstrip("#")
    rgb = [int(color[index:index + 2], 16) for index in (0, 2, 4)] if len(color) == 6 else [255, 255, 255]
    alpha = image_rgba[:, :, 3:4].astype(np.float32) / 255.0
    foreground = image_rgba[:, :, :3].astype(np.float32)
    return (foreground * alpha + np.array(rgb, dtype=np.float32) * (1 - alpha)).astype(np.uint8)


def synthesize_image(source: np.ndarray, reference, rois, *, mode: str, model_id: str,
                     device: str, prompt: str, progress_callback: ProgressCallback | None = None):
    from core.synthesizer import Synthesizer
    return Synthesizer(
        mode=mode, model_id=model_id, device=device, prompt=prompt,
        progress_callback=progress_callback,
    ).run(source_rgb=source, rois=rois, reference_rgb=reference)


def postprocess_image(original: np.ndarray, inpainted: np.ndarray, mask: np.ndarray,
                      method: str, device: str):
    from core.background_fixer import fix_background
    return fix_background(original_rgb=original, inpainted_rgb=inpainted,
                          mask=mask, method=method, device=device)


def pipeline_image(original: np.ndarray, mask, rois, options: dict,
                   progress_callback: ProgressCallback | None = None):
    from core.pipeline import Pipeline, PipelineConfig, InpaintStep, PostprocessStep, UpscaleStep
    config = PipelineConfig(
        inpaint=InpaintStep(model=options["inpaint_model"], device=options["device"],
                           dilation=options["dilation"], disable_nsfw=options["disable_nsfw"]),
        postprocess=PostprocessStep(method=options["postprocess_method"], device=options["device"],
                                   enabled=options["postprocess_enabled"]),
        upscale=UpscaleStep(model=options["upscale_model"], device=options["device"],
                            enabled=options["upscale_enabled"]),
    )
    labels = {"inpaint": "水印修复", "postprocess": "后处理", "upscale": "超分辨率", "done": "处理完成"}

    def report(stage: str, percent: int):
        if progress_callback:
            progress_callback(percent, labels.get(stage, stage))

    return Pipeline(config).run(original_rgb=original, mask=mask,
                                rois=[tuple(roi) for roi in rois] if rois else None,
                                progress_callback=report)
