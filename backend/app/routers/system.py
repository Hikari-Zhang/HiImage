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
    """检测当前环境中各计算设备的可用性，并返回不可用时的诊断原因"""
    mps_available = False
    mps_reason = ""
    cuda_available = False
    cuda_count = 0
    cuda_name = ""
    cuda_reason = ""

    try:
        import torch
        torch_version = torch.__version__

        # ── MPS ──────────────────────────────────────────────────────────
        if hasattr(torch.backends, "mps"):
            if torch.backends.mps.is_available():
                mps_available = True
            else:
                # macOS 但 MPS 不可用（系统版本过低或非 Apple Silicon）
                import platform
                mps_reason = "需要 Apple Silicon 芯片且 macOS 12.3+"
        else:
            mps_reason = "非 macOS 环境"

        # ── CUDA ─────────────────────────────────────────────────────────
        if torch.cuda.is_available():
            cuda_available = True
            cuda_count = torch.cuda.device_count()
            try:
                cuda_name = torch.cuda.get_device_name(0)
            except Exception:
                cuda_name = "CUDA Device"
        else:
            # 区分「PyTorch 是 CPU-only 版」vs「驱动/硬件问题」
            # torch.version.cuda 为 None 说明编译时根本没有 CUDA 支持
            if torch.version.cuda is None:
                cuda_reason = (
                    f"当前 PyTorch {torch_version} 为 CPU-only 版本，"
                    "请重装 CUDA 版：pip install torch --index-url https://download.pytorch.org/whl/cu121"
                )
            else:
                # 有 CUDA 编译支持，但运行时检测失败 → 驱动或硬件问题
                built_cuda = torch.version.cuda
                cuda_reason = (
                    f"PyTorch 编译版本 CUDA {built_cuda}，但未检测到可用 GPU。"
                    f"请确认：① NVIDIA 驱动已安装  ② 驱动版本与 CUDA {built_cuda} 兼容"
                )

    except ImportError:
        mps_reason = "torch 未安装"
        cuda_reason = "torch 未安装"

    return {
        "devices": [
            {
                "id": "mps",
                "label": "MPS",
                "desc": "Apple Silicon",
                "available": mps_available,
                "reason": mps_reason,
            },
            {
                "id": "cpu",
                "label": "CPU",
                "desc": "通用（较慢）",
                "available": True,
                "reason": "",
            },
            {
                "id": "cuda",
                "label": "CUDA",
                "desc": cuda_name or "NVIDIA GPU",
                "available": cuda_available,
                "device_count": cuda_count,
                "reason": cuda_reason,
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
    from core.model_registry import get_models_for_mode

    # 从注册表读取 upscale 模式下的所有模型（与 upscale.py 保持一致）
    models = get_models_for_mode("upscale")

    # 按 display_group 顺序分组（保留 YAML 中的顺序）
    groups_dict: dict[str, list] = {}
    for m in models:
        label = m.get("display_group", "其他")
        if label not in groups_dict:
            groups_dict[label] = []
        groups_dict[label].append({
            "id": m["id"],
            "name": m.get("name", m["id"]),
            "description": m.get("description", ""),
            "scale": m.get("scale", 4),
            "outscale": m.get("outscale", m.get("scale", 4)),
            "supports_custom_outscale": m.get("supports_custom_outscale", False),
        })

    return {
        "groups": [
            {"label": label, "models": model_list}
            for label, model_list in groups_dict.items()
        ]
    }
