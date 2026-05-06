"""
系统路由 - 健康检查、模型列表
"""
from fastapi import APIRouter

from core.inpainter import MODEL_GROUPS
from core.upscaler import UPSCALE_MODEL_GROUPS, _MODEL_SCALE

router = APIRouter()


@router.get("/health")
async def health_check():
    return {"status": "ok", "version": "2.0.0"}


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
