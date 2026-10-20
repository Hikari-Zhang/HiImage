"""
水印去除路由 - 检测 & 修复
"""
import asyncio
import base64
import io
from typing import List, Optional

import cv2
import numpy as np
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import get
from app.shared_executor import inference_admission, inference_scheduler
from app.task_id import TaskId
from app.websocket.progress import progress_manager
from app.logging_manager import log_manager
from core.model_registry import get_default_model
from core.utils import make_progress_callback, resolve_default_device

router = APIRouter()


# ============ 模型检查辅助函数 ============

async def ensure_model_ready_with_progress(
    model_id: str, operation_name: str, task_id: TaskId, source: str = "inpaint"
) -> tuple[bool, str]:
    """
    检查并确保模型已下载，支持进度推送。

    :param model_id: 模型 ID
    :param operation_name: 操作名称（用于日志和进度消息）
    :param task_id: 任务 ID，用于隔离 WebSocket 频道
    :param source: 日志来源标识
    :return: (success, error_message)
    """
    from core.model_download_helper import ensure_model_ready

    async def _progress_callback(percent: int, message: str):
        try:
            if percent >= 0:
                await progress_manager.send_progress(
                    max(5, min(percent, 30)),
                    f"{operation_name}：{message}",
                    task_id=task_id,
                )
            else:
                await progress_manager.send_error(message, task_id=task_id)
        except Exception as e:
            log_manager.error(f"进度推送失败: {e}", source=source)

    log_manager.info(f"[{operation_name}] 检查模型就绪状态: {model_id}", source=source)
    success, error_msg = await ensure_model_ready(
        model_id=model_id,
        progress_callback=_progress_callback,
    )

    if not success:
        log_manager.error(f"[{operation_name}] 模型未就绪: {model_id} - {error_msg}", source=source)

    return success, error_msg


# ============ Request/Response Models ============


class DetectRequest(BaseModel):
    image: str  # Base64 encoded PNG/JPG
    sensitivity: float = 0.5
    task_id: TaskId


class InpaintRequest(BaseModel):
    image: str  # Base64 encoded
    rois: List[List[int]]  # [[x1, y1, x2, y2], ...]
    model: str = Field(default_factory=lambda: get_default_model("watermark_removal"))
    device: str = Field(default_factory=lambda: resolve_default_device(get("inpaint.default_device")))
    dilation: int = 10
    disable_nsfw: bool = False
    task_id: TaskId


class InpaintWithMaskRequest(BaseModel):
    image: str  # Base64 encoded
    mask: str  # Base64 encoded mask
    model: str = Field(default_factory=lambda: get_default_model("watermark_removal"))
    device: str = Field(default_factory=lambda: resolve_default_device(get("inpaint.default_device")))
    dilation: int = 10
    disable_nsfw: bool = False
    task_id: TaskId


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


# 单张图像最大允许解码大小（字节）。
# Base64 字符数 × 3/4 ≈ 解码后字节数；30 MB 解码后约占 ~150 MB 峰值内存
# （base64 字符串 + 解码字节 + RGB numpy 各一份），
# 可通过 app/config.py 的 image.max_decode_mb 覆盖。
_DEFAULT_IMAGE_MAX_MB = 30


def check_image_size(
    base64_str: str,
    max_mb: Optional[int] = None,
) -> Optional[JSONResponse]:
    """
    估算 Base64 图像的解码大小，超出阈值时返回 413 JSONResponse，否则返回 None。

    在各路由入口调用，避免超大图像在解码后才发现内存不足（此时已无法优雅拒绝）。

    :param base64_str: 原始 Base64 字符串（可含 data URL 前缀）
    :param max_mb:     解码后最大允许大小（MB）；None 取默认值 _DEFAULT_IMAGE_MAX_MB
    :return:           超出限制时返回 JSONResponse(413)，合规时返回 None
    """
    if max_mb is None:
        max_mb = int(get("image.max_decode_mb", _DEFAULT_IMAGE_MAX_MB))
    raw = base64_str.split(",", 1)[1] if "," in base64_str else base64_str
    # Base64 每 4 字符对应 3 字节，快速估算无需实际解码
    estimated_bytes = len(raw) * 3 // 4
    limit_bytes = max_mb * 1024 * 1024
    if estimated_bytes > limit_bytes:
        msg = (
            f"图像过大：估算解码后约 {estimated_bytes // (1024 * 1024)} MB，"
            f"超出上限 {max_mb} MB。请压缩图像后重试。"
        )
        log_manager.warning(msg, source="inpaint")
        return JSONResponse(
            status_code=413,
            content={"detail": msg, "message": msg},
        )
    return None


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


@router.post("/detect", dependencies=[Depends(inference_admission)])
async def detect_watermark(req: DetectRequest):
    """自动检测水印区域"""
    from core.watermark_detector import WatermarkDetector

    if (err := check_image_size(req.image)):
        return err

    image = decode_image(req.image)
    log_manager.info(f"开始水印检测 (sensitivity={req.sensitivity})", source="inpaint")

    def _detect():
        detector = WatermarkDetector(sensitivity=req.sensitivity)
        return detector.detect(image)

    regions = await inference_scheduler.run_blocking(_detect)

    log_manager.info(f"水印检测完成，发现 {len(regions)} 个区域", source="inpaint")
    return {
        "regions": [[int(x1), int(y1), int(x2), int(y2)] for x1, y1, x2, y2 in regions]
    }


@router.post("/inpaint", dependencies=[Depends(inference_admission)])
async def inpaint_with_rois(req: InpaintRequest):
    """使用 ROI 列表去除水印"""
    if (err := check_image_size(req.image)):
        return err

    image = decode_image(req.image)
    log_manager.info(
        f"开始水印去除: model={req.model}, device={req.device}, "
        f"rois={len(req.rois)}, dilation={req.dilation}",
        source="inpaint",
    )

    await progress_manager.send_progress(5, "正在检查模型...", task_id=req.task_id)
    success, error_msg = await ensure_model_ready_with_progress(req.model, "水印去除", req.task_id)
    if not success:
        await progress_manager.send_error(error_msg, task_id=req.task_id)
        log_manager.error(f"水印去除失败（模型未就绪）: {error_msg}", source="inpaint")
        return JSONResponse(
            status_code=422,
            content={"detail": error_msg, "message": error_msg}
        )

    await progress_manager.send_progress(10, "正在初始化模型...", task_id=req.task_id)

    loop = asyncio.get_running_loop()

    def _process():
        from core.model_registry import get_model
        from core.model_executor import ModelExecutorFactory

        model_config = get_model(req.model)

        model_exec = ModelExecutorFactory.create_executor(model_config, req.device)

        if model_exec.supports_mask():
            # IOPaint 类模型：需要 mask 或 rois
            return model_exec.execute(
                image,
                rois=req.rois,
                progress_callback=make_progress_callback(req.task_id, loop),
            )
        else:
            # Restormer / NAFNet 类模型：直接处理（不需要 mask）
            return model_exec.execute(
                image,
                progress_callback=make_progress_callback(req.task_id, loop),
            )

    await progress_manager.send_progress(20, "正在处理...", task_id=req.task_id)

    try:
        result = await inference_scheduler.run_blocking(_process)
        await progress_manager.send_complete(task_id=req.task_id)
        log_manager.info("水印去除完成", source="inpaint")
        return {"image": encode_image(result)}
    except Exception as e:
        err_str = str(e)
        await progress_manager.send_error(err_str, task_id=req.task_id)
        log_manager.error(f"水印去除失败: {err_str}", source="inpaint")
        first_line = err_str.split('\n')[0]
        return JSONResponse(status_code=422, content={"detail": err_str, "message": first_line})


@router.post("/inpaint/with-mask", dependencies=[Depends(inference_admission)])
async def inpaint_with_mask(req: InpaintWithMaskRequest):
    """使用自定义掩码去除水印"""
    if (err := check_image_size(req.image)):
        return err

    image = decode_image(req.image)
    mask = decode_mask(req.mask)
    log_manager.info(
        f"开始掩码修复: model={req.model}, device={req.device}",
        source="inpaint",
    )

    await progress_manager.send_progress(5, "正在检查模型...", task_id=req.task_id)
    success, error_msg = await ensure_model_ready_with_progress(req.model, "掩码修复", req.task_id)
    if not success:
        await progress_manager.send_error(error_msg, task_id=req.task_id)
        log_manager.error(f"掩码修复失败（模型未就绪）: {error_msg}", source="inpaint")
        return JSONResponse(
            status_code=422,
            content={"detail": error_msg, "message": error_msg}
        )

    await progress_manager.send_progress(10, "正在初始化模型...", task_id=req.task_id)

    loop = asyncio.get_running_loop()

    def _process():
        from core.model_registry import get_model
        from core.model_executor import ModelExecutorFactory

        model_config = get_model(req.model)

        model_exec = ModelExecutorFactory.create_executor(model_config, req.device)

        return model_exec.execute(
            image,
            mask=mask,
            progress_callback=make_progress_callback(req.task_id, loop),
        )

    await progress_manager.send_progress(20, "正在处理...", task_id=req.task_id)

    try:
        result = await inference_scheduler.run_blocking(_process)
        await progress_manager.send_complete(task_id=req.task_id)
        log_manager.info("掩码修复完成", source="inpaint")
        return {"image": encode_image(result)}
    except Exception as e:
        err_str = str(e)
        await progress_manager.send_error(err_str, task_id=req.task_id)
        log_manager.error(f"掩码修复失败: {err_str}", source="inpaint")
        first_line = err_str.split('\n')[0]
        return JSONResponse(status_code=422, content={"detail": err_str, "message": first_line})
