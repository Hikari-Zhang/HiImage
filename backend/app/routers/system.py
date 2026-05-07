"""
系统路由 - 健康检查、设备检测、模型列表
"""
from fastapi import APIRouter

from core.inpainter import MODEL_GROUPS
from core.upscaler import UPSCALE_MODEL_GROUPS, _MODEL_SCALE

router = APIRouter()


@router.get("/health")
async def health_check():
    return {"status": "ok", "version": "2.0.0"}


@router.get("/devices")
async def available_devices():
    """检测当前环境中各计算设备的可用性"""
    mps_available = False
    cuda_available = False
    cuda_count = 0
    cuda_name = ""

    try:
        import torch
        mps_available = bool(
            hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
        )
        cuda_available = bool(torch.cuda.is_available())
        if cuda_available:
            cuda_count = torch.cuda.device_count()
            try:
                cuda_name = torch.cuda.get_device_name(0)
            except Exception:
                cuda_name = "CUDA Device"
    except ImportError:
        pass

    return {
        "devices": [
            {
                "id": "mps",
                "label": "MPS",
                "desc": "Apple Silicon",
                "available": mps_available,
            },
            {
                "id": "cpu",
                "label": "CPU",
                "desc": "通用（较慢）",
                "available": True,
            },
            {
                "id": "cuda",
                "label": "CUDA",
                "desc": cuda_name or "NVIDIA GPU",
                "available": cuda_available,
                "device_count": cuda_count,
            },
        ]
    }


@router.get("/models/inpaint")
async def list_inpaint_models():
    """获取修复模型列表（分组）"""
    groups = []
    for group_label, models in MODEL_GROUPS:
        items = [
            {"id": mid, "name": display_name, "description": desc}
            for mid, display_name, desc in models
        ]
        groups.append({"label": group_label, "models": items})
    return {"groups": groups}


@router.get("/models/upscale")
async def list_upscale_models():
    """获取超分辨率模型列表（分组）"""
    groups = []
    for group_label, models in UPSCALE_MODEL_GROUPS:
        items = [
            {
                "id": model_name,
                "name": display_name,
                "description": desc,
                "scale": _MODEL_SCALE.get(model_name, 4),
            }
            for model_name, display_name, desc in models
        ]
        groups.append({"label": group_label, "models": items})
    return {"groups": groups}
