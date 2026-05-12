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
from pydantic import BaseModel, Field

from app.config import get
from app.websocket.progress import progress_manager
from app.logging_manager import log_manager
from core.model_registry import get_default_model

router = APIRouter()
executor = ThreadPoolExecutor(max_workers=1)


# ============ 模型检查辅助函数 ============

async def _ensure_model_ready_with_progress(model_id: str, operation_name: str) -> tuple[bool, str]:
    """
    检查并确保模型已下载，支持进度推送。
    
    :param model_id: 模型 ID
    :param operation_name: 操作名称（用于日志和进度消息）
    :return: (success, error_message)
    """
    from core.model_download_helper import ensure_model_ready
    
    async def _progress_callback(percent: int, message: str):
        """进度回调函数，通过 WebSocket 推送进度"""
        try:
            if percent >= 0:
                await progress_manager.send_progress(
                    max(5, min(percent, 30)),  # 限制在 5-30% 范围内
                    f"{operation_name}：{message}"
                )
            else:
                await progress_manager.send_error(message)
        except Exception as e:
            log_manager.error(f"进度推送失败: {e}", source="inpaint")
    
    log_manager.info(f"[{operation_name}] 检查模型就绪状态: {model_id}", source="inpaint")
    success, error_msg = await ensure_model_ready(
        model_id=model_id,
        progress_callback=_progress_callback,
    )
    
    if not success:
        log_manager.error(f"[{operation_name}] 模型未就绪: {model_id} - {error_msg}", source="inpaint")
    
    return success, error_msg


# ============ Request/Response Models ============


class DetectRequest(BaseModel):
    image: str  # Base64 encoded PNG/JPG
    sensitivity: float = 0.5


class InpaintRequest(BaseModel):
    image: str  # Base64 encoded
    rois: List[List[int]]  # [[x1, y1, x2, y2], ...]
    model: str = Field(default_factory=lambda: get_default_model("watermark_removal"))
    device: str = "mps"
    dilation: int = 10
    disable_nsfw: bool = False


class InpaintWithMaskRequest(BaseModel):
    image: str  # Base64 encoded
    mask: str  # Base64 encoded mask
    model: str = Field(default_factory=lambda: get_default_model("watermark_removal"))
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


@router.get("/models/inpaint")
async def get_inpaint_models():
    """
    返回所有去水印模型（来自 models.yaml），按 display_group 分组。

    响应格式与前端 useModelStore.ts 对齐：
    {
      "groups": [
        {
          "label": "快速本地模型",
          "models": [{"id": "wm_lama", "name": "LaMa", "description": "..."}]
        }
      ]
    }
    """
    from core.model_registry import get_models_for_mode

    # 从注册表读取 watermark_removal 模式下的所有模型
    models = get_models_for_mode("watermark_removal")

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
            "badge":       m.get("badge", ""),
        })

    return {
        "groups": [
            {"label": label, "models": model_list}
            for label, model_list in groups.items()
        ]
    }


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

    # 检查并确保模型已下载
    await progress_manager.send_progress(5, f"正在检查模型...")
    success, error_msg = await _ensure_model_ready_with_progress(req.model, "水印去除")
    if not success:
        await progress_manager.send_error(error_msg)
        log_manager.error(f"水印去除失败（模型未就绪）: {error_msg}", source="inpaint")
        return JSONResponse(
            status_code=422,
            content={"detail": error_msg, "message": error_msg}
        )

    await progress_manager.send_progress(10, "正在初始化模型...")

    loop = asyncio.get_event_loop()

    def _make_progress_callback():
        """创建线程安全的进度回调"""
        def callback(percent: int, message: str):
            try:
                asyncio.run_coroutine_threadsafe(
                    progress_manager.send_progress(percent, message),
                    loop
                )
            except Exception as e:
                log_manager.error(f"进度上报失败: {e}", source="inpaint")
        return callback

    def _process():
        # 使用新的执行器框架（支持 IOPaint / Restormer / NAFNet 等）
        from core.model_registry import get_model
        from core.model_executor import ModelExecutorFactory

        # 获取模型配置
        model_config = get_model(req.model)

        # 创建对应的执行器（自动根据 provider 分发）
        executor = ModelExecutorFactory.create_executor(model_config, req.device)

        # 统一调用接口
        if executor.supports_mask():
            # IOPaint 类模型：需要 mask 或 rois
            return executor.execute(image, rois=req.rois, progress_callback=_make_progress_callback())
        else:
            # Restormer / NAFNet 类模型：直接处理（不需要 mask）
            return executor.execute(image, progress_callback=_make_progress_callback())

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

    # 检查并确保模型已下载
    await progress_manager.send_progress(5, f"正在检查模型...")
    success, error_msg = await _ensure_model_ready_with_progress(req.model, "掩码修复")
    if not success:
        await progress_manager.send_error(error_msg)
        log_manager.error(f"掩码修复失败（模型未就绪）: {error_msg}", source="inpaint")
        return JSONResponse(
            status_code=422,
            content={"detail": error_msg, "message": error_msg}
        )

    await progress_manager.send_progress(10, "正在初始化模型...")

    loop = asyncio.get_event_loop()

    def _make_progress_callback():
        """创建线程安全的进度回调（子线程 → 主线程事件循环）"""
        def callback(percent: int, message: str):
            try:
                asyncio.run_coroutine_threadsafe(
                    progress_manager.send_progress(percent, message),
                    loop
                )
            except Exception as e:
                log_manager.error(f"进度上报失败: {e}", source="inpaint")
        return callback

    def _process():
        # 使用新的执行器框架（支持 IOPaint / Restormer / NAFNet 等）
        from core.model_registry import get_model
        from core.model_executor import ModelExecutorFactory

        # 获取模型配置
        model_config = get_model(req.model)

        # 创建对应的执行器（自动根据 provider 分发）
        executor = ModelExecutorFactory.create_executor(model_config, req.device)

        # 统一调用接口
        return executor.execute(image, mask=mask, progress_callback=_make_progress_callback())

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
