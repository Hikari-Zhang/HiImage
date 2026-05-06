"""
水印去除路由 - 检测 & 修复
"""
import asyncio
import base64
import io
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

import cv2
import numpy as np
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import get
from app.websocket.progress import progress_manager
from app.logging_manager import log_manager

router = APIRouter()
executor = ThreadPoolExecutor(max_workers=1)


# ============ Request/Response Models ============


class DetectRequest(BaseModel):
    image: str  # Base64 encoded PNG/JPG
    sensitivity: float = 0.5


class InpaintRequest(BaseModel):
    image: str  # Base64 encoded
    rois: List[List[int]]  # [[x1, y1, x2, y2], ...]
    model: str = "lama"
    device: str = "mps"
    dilation: int = 10
    disable_nsfw: bool = False


class InpaintWithMaskRequest(BaseModel):
    image: str  # Base64 encoded
    mask: str  # Base64 encoded mask
    model: str = "lama"
    device: str = "mps"
    dilation: int = 10
    disable_nsfw: bool = False


# ============ Helper Functions ============


def decode_image(base64_str: str) -> np.ndarray:
    """Base64 → RGB numpy array"""
    # 去除 data URL prefix（如 "data:image/png;base64,"）
    if "," in base64_str:
        base64_str = base64_str.split(",", 1)[1]
    img_bytes = base64.b64decode(base64_str)
    nparr = np.frombuffer(img_bytes, np.uint8)
    img_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img_bgr is None:
        raise ValueError("无法解码图像")
    return cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)


def decode_mask(base64_str: str) -> np.ndarray:
    """Base64 → 灰度 mask numpy array"""
    if "," in base64_str:
        base64_str = base64_str.split(",", 1)[1]
    img_bytes = base64.b64decode(base64_str)
    nparr = np.frombuffer(img_bytes, np.uint8)
    mask = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise ValueError("无法解码掩码")
    return mask


def encode_image(image_rgb: np.ndarray) -> str:
    """RGB numpy array → Base64 PNG"""
    img_bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
    _, buffer = cv2.imencode(".png", img_bgr)
    return base64.b64encode(buffer).decode("utf-8")


# ============ Endpoints ============


@router.post("/detect")
async def detect_watermark(req: DetectRequest):
    """自动检测水印区域"""
    from core.watermark_detector import WatermarkDetector

    image = decode_image(req.image)
    log_manager.info(f"开始水印检测 (sensitivity={req.sensitivity})", source="inpaint")

    def _detect():
        detector = WatermarkDetector(sensitivity=req.sensitivity)
        return detector.detect(image)

    loop = asyncio.get_event_loop()
    regions = await loop.run_in_executor(executor, _detect)

    log_manager.info(f"水印检测完成，发现 {len(regions)} 个区域", source="inpaint")
    return {
        "regions": [[int(x1), int(y1), int(x2), int(y2)] for x1, y1, x2, y2 in regions]
    }


@router.post("/inpaint")
async def inpaint_with_rois(req: InpaintRequest):
    """使用 ROI 列表去除水印"""
    from core.inpainter import Inpainter

    image = decode_image(req.image)
    log_manager.info(
        f"开始水印去除: model={req.model}, device={req.device}, "
        f"rois={len(req.rois)}, dilation={req.dilation}",
        source="inpaint",
    )

    await progress_manager.send_progress(10, "正在初始化模型...")

    def _process():
        inpainter = Inpainter(
            model_name=req.model,
            device=req.device,
            dilation=req.dilation,
            disable_nsfw=req.disable_nsfw,
        )
        return inpainter.remove_watermark(image, req.rois)

    loop = asyncio.get_event_loop()
    await progress_manager.send_progress(20, "正在处理...")

    try:
        result = await loop.run_in_executor(executor, _process)
        await progress_manager.send_complete()
        log_manager.info("水印去除完成", source="inpaint")
        return {"image": encode_image(result)}
    except Exception as e:
        err_str = str(e)
        await progress_manager.send_error(err_str)
        log_manager.error(f"水印去除失败: {err_str}", source="inpaint")
        # 提取第一行作为简短标题，避免把整个堆栈甩给前端
        first_line = err_str.split('\n')[0]
        return JSONResponse(status_code=422, content={"detail": err_str, "message": first_line})


@router.post("/inpaint/with-mask")
async def inpaint_with_mask(req: InpaintWithMaskRequest):
    """使用自定义掩码去除水印"""
    from core.inpainter import Inpainter

    image = decode_image(req.image)
    mask = decode_mask(req.mask)
    log_manager.info(
        f"开始掩码修复: model={req.model}, device={req.device}",
        source="inpaint",
    )

    await progress_manager.send_progress(10, "正在初始化模型...")

    def _process():
        inpainter = Inpainter(
            model_name=req.model,
            device=req.device,
            dilation=req.dilation,
            disable_nsfw=req.disable_nsfw,
        )
        return inpainter.remove_watermark_with_mask(image, mask)

    loop = asyncio.get_event_loop()
    await progress_manager.send_progress(20, "正在处理...")

    try:
        result = await loop.run_in_executor(executor, _process)
        await progress_manager.send_complete()
        log_manager.info("掩码修复完成", source="inpaint")
        return {"image": encode_image(result)}
    except Exception as e:
        err_str = str(e)
        await progress_manager.send_error(err_str)
        log_manager.error(f"掩码修复失败: {err_str}", source="inpaint")
        first_line = err_str.split('\n')[0]
        return JSONResponse(status_code=422, content={"detail": err_str, "message": first_line})
