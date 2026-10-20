"""
抠图路由

提供：
  GET  /api/cutout/models       获取抠图模型列表
  POST /api/cutout              执行抠图（返回 RGBA 结果 + alpha mask）
  POST /api/cutout/refine       遮罩精细化（羽化/膨胀/腐蚀/画笔编辑）
  POST /api/cutout/composite    合成到自定义背景色
"""
import asyncio
import base64
from typing import List, Optional

import cv2
import numpy as np
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.routers.inpaint import decode_image, encode_image, check_image_size, ensure_model_ready_with_progress
from app.shared_executor import executor
from app.websocket.progress import progress_manager
from app.logging_manager import log_manager
from app.config import get
from core.utils import resolve_default_device

router = APIRouter()


# ============ 编解码辅助函数 ============

def encode_rgba_image(image_rgba: np.ndarray) -> str:
    """RGBA numpy array → Base64 PNG"""
    img_bgra = cv2.cvtColor(image_rgba, cv2.COLOR_RGBA2BGRA)
    _, buffer = cv2.imencode(".png", img_bgra)
    return base64.b64encode(buffer).decode("utf-8")


def encode_mask(mask: np.ndarray) -> str:
    """灰度 mask numpy array → Base64 PNG"""
    _, buffer = cv2.imencode(".png", mask)
    return base64.b64encode(buffer).decode("utf-8")


def decode_mask(base64_str: str) -> np.ndarray:
    """Base64 → 灰度 mask numpy array"""
    if "," in base64_str:
        base64_str = base64_str.split(",", 1)[1]
    img_bytes = base64.b64decode(base64_str)
    nparr = np.frombuffer(img_bytes, np.uint8)
    mask = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise ValueError("无法解码遮罩")
    return mask


# ──────────────────────────────────────────────────────────────
# Request / Response Models
# ──────────────────────────────────────────────────────────────

class CutoutRequest(BaseModel):
    """抠图请求"""
    image: str              # Base64 原图
    model: str = "birefnet" # 模型 ID
    device: str = Field(default_factory=lambda: resolve_default_device(get("inpaint.default_device")))     # 推理设备
    task_id: str = ""       # WebSocket 隔离频道 ID（客户端生成，空=全局广播）


class RefineRequest(BaseModel):
    """遮罩精细化请求"""
    mask: str                           # Base64 alpha mask (从上次抠图结果或前端编辑后)
    original_image: str                 # Base64 原图 (用于合成)
    feather: int = 0                    # 羽化像素 0-30
    dilate: int = 0                     # 膨胀(正)/腐蚀(负)像素 -20~20
    brush_add: Optional[str] = None     # Base64 画笔添加区域(白=添加前景)
    brush_remove: Optional[str] = None  # Base64 画笔擦除区域(白=擦除前景)


class CompositeRequest(BaseModel):
    """合成到自定义背景色请求"""
    image: str          # Base64 RGBA 结果图
    background_color: str = "#FFFFFF"  # 十六进制背景色


# ──────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────

@router.get("/cutout/models")
async def get_cutout_models():
    """
    获取抠图模型列表（来自 models.yaml），按 display_group 分组。

    响应格式：
    {
      "groups": [
        {
          "label": "通用抠图",
          "models": [{"id": "birefnet", "name": "BiRefNet-General", ...}]
        }
      ]
    }
    """
    from core.model_registry import get_models_for_mode, get_default_model

    cutout_models = get_models_for_mode("cutout")

    # 按 display_group 分组
    groups_dict: dict[str, list] = {}
    for m in cutout_models:
        group = m.get("display_group", "通用抠图")
        if group not in groups_dict:
            groups_dict[group] = []
        groups_dict[group].append({
            "id": m["id"],
            "name": m.get("name", m["id"]),
            "description": m.get("description", ""),
            "recommended_for": m.get("recommended_for", ""),
            "size_mb": m.get("size_mb"),
            "badge": m.get("badge", ""),
            "provider": m.get("provider", ""),
        })

    groups = [{"label": label, "models": models_list} for label, models_list in groups_dict.items()]

    # 获取默认模型
    try:
        default_model = get_default_model("cutout")
    except KeyError:
        default_model = "birefnet"

    return {"groups": groups, "default_model": default_model}


@router.post("/cutout")
async def run_cutout(req: CutoutRequest):
    """执行抠图，返回 RGBA 结果图 + alpha mask"""
    if (err := check_image_size(req.image)):
        return err

    source = decode_image(req.image)

    log_manager.info(
        f"开始抠图: model={req.model}, device={req.device}, "
        f"image_size={source.shape[1]}x{source.shape[0]}",
        source="cutout",
    )

    # 检查模型是否就绪
    await progress_manager.send_progress(5, "正在检查模型...", task_id=req.task_id)
    success, error_msg = await ensure_model_ready_with_progress(req.model, "抠图", req.task_id, source="cutout")
    if not success:
        await progress_manager.send_error(error_msg, task_id=req.task_id)
        return JSONResponse(
            status_code=422,
            content={"detail": error_msg, "message": error_msg}
        )

    await progress_manager.send_progress(10, "正在初始化抠图模型...", task_id=req.task_id)

    loop = asyncio.get_running_loop()

    def _process():
        from core.model_executor import ModelExecutorFactory
        from core.model_registry import get_model

        model_config = get_model(req.model)

        model_executor = ModelExecutorFactory.create_executor(model_config, req.device)
        try:
            # RembgExecutor.execute() 返回 RGBA numpy array
            result_rgba = model_executor.execute(source)
            return result_rgba
        finally:
            model_executor.unload_model()
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

    await progress_manager.send_progress(20, "正在抠图处理...", task_id=req.task_id)

    try:
        result_rgba = await loop.run_in_executor(executor, _process)
        h, w = result_rgba.shape[:2]

        # 提取 alpha mask
        if result_rgba.shape[2] == 4:
            alpha_mask = result_rgba[:, :, 3]
        else:
            # 如果没有 alpha 通道，创建全白 mask
            alpha_mask = np.full((h, w), 255, dtype=np.uint8)

        await progress_manager.send_complete("抠图完成", task_id=req.task_id)
        log_manager.info(f"抠图完成: output={w}x{h}", source="cutout")

        return {
            "image": encode_rgba_image(result_rgba),
            "mask": encode_mask(alpha_mask),
            "width": w,
            "height": h,
        }
    except Exception as e:
        err_str = str(e)
        await progress_manager.send_error(err_str, task_id=req.task_id)
        log_manager.error(f"抠图失败: {err_str}", source="cutout")
        first_line = err_str.split("\n")[0]
        return JSONResponse(
            status_code=422,
            content={"detail": err_str, "message": first_line},
        )


@router.post("/cutout/refine")
async def refine_mask(req: RefineRequest):
    """
    遮罩精细化：羽化、膨胀/腐蚀、画笔编辑。

    纯 OpenCV 处理，无需 GPU。
    """
    if (err := check_image_size(req.original_image)):
        return err

    mask = decode_mask(req.mask)
    original = decode_image(req.original_image)

    log_manager.info(
        f"遮罩精修: feather={req.feather}, dilate={req.dilate}, "
        f"has_brush_add={req.brush_add is not None}, "
        f"has_brush_remove={req.brush_remove is not None}",
        source="cutout",
    )

    def _process():
        nonlocal mask
        # 1. 画笔编辑
        if req.brush_add is not None:
            brush_add = decode_mask(req.brush_add)
            # 调整尺寸以防不匹配
            if brush_add.shape != mask.shape:
                brush_add = cv2.resize(brush_add, (mask.shape[1], mask.shape[0]))
            mask[brush_add > 128] = 255

        if req.brush_remove is not None:
            brush_remove = decode_mask(req.brush_remove)
            if brush_remove.shape != mask.shape:
                brush_remove = cv2.resize(brush_remove, (mask.shape[1], mask.shape[0]))
            mask[brush_remove > 128] = 0

        # 2. 膨胀/腐蚀
        if req.dilate != 0:
            kernel_size = abs(req.dilate) * 2 + 1
            kernel = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (kernel_size, kernel_size)
            )
            if req.dilate > 0:
                mask = cv2.dilate(mask, kernel, iterations=1)
            else:
                mask = cv2.erode(mask, kernel, iterations=1)

        # 3. 羽化
        if req.feather > 0:
            # 对 mask 进行高斯模糊实现羽化效果
            blur_size = req.feather * 2 + 1
            mask = cv2.GaussianBlur(mask, (blur_size, blur_size), 0)
            # 保留中间灰度值，不做二值化，以获得平滑过渡

        # 4. 用精修 mask 合成 RGBA 结果
        h, w = original.shape[:2]
        # 确保 mask 尺寸与原图一致
        if mask.shape != (h, w):
            mask = cv2.resize(mask, (w, h))

        result_rgba = np.dstack([original, mask])

        return result_rgba, mask

    try:
        result_rgba, refined_mask = _process()
        h, w = result_rgba.shape[:2]

        log_manager.info(f"遮罩精修完成: output={w}x{h}", source="cutout")

        return {
            "image": encode_rgba_image(result_rgba),
            "mask": encode_mask(refined_mask),
            "width": w,
            "height": h,
        }
    except Exception as e:
        err_str = str(e)
        log_manager.error(f"遮罩精修失败: {err_str}", source="cutout")
        return JSONResponse(
            status_code=422,
            content={"detail": err_str, "message": err_str.split("\n")[0]},
        )


@router.post("/cutout/composite")
async def composite_cutout(req: CompositeRequest):
    """
    将抠图结果合成到自定义背景色上。

    将 RGBA 结果图与纯色背景合成，返回 RGB 结果。
    """
    # 解码 RGBA 图像
    if "," in req.image:
        base64_str = req.image.split(",", 1)[1]
    else:
        base64_str = req.image
    img_bytes = base64.b64decode(base64_str)
    nparr = np.frombuffer(img_bytes, np.uint8)
    img_bgra = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)

    if img_bgra is None:
        return JSONResponse(
            status_code=422,
            content={"detail": "无法解码图像", "message": "无法解码图像"},
        )

    def _process():
        # BGRA → RGB
        if img_bgra.shape[2] == 4:
            alpha = img_bgra[:, :, 3:4].astype(np.float32) / 255.0
            bgr = img_bgra[:, :, :3].astype(np.float32)
        else:
            alpha = np.ones((*img_bgra.shape[:2], 1), dtype=np.float32)
            bgr = img_bgra.astype(np.float32)

        # 解析背景色
        hex_color = req.background_color.lstrip("#")
        if len(hex_color) == 6:
            r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
        else:
            r, g, b = 255, 255, 255

        # OpenCV 用 BGR
        bg = np.array([b, g, r], dtype=np.float32)
        bg_layer = np.full_like(bgr, bg)

        # Alpha blending
        result_bgr = (bgr * alpha + bg_layer * (1 - alpha)).astype(np.uint8)
        result_rgb = cv2.cvtColor(result_bgr, cv2.COLOR_BGR2RGB)

        return result_rgb

    try:
        result = _process()
        h, w = result.shape[:2]

        return {
            "image": encode_image(result),
            "width": w,
            "height": h,
        }
    except Exception as e:
        err_str = str(e)
        log_manager.error(f"合成失败: {err_str}", source="cutout")
        return JSONResponse(
            status_code=422,
            content={"detail": err_str, "message": err_str.split("\n")[0]},
        )
