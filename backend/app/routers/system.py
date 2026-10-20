"""
系统路由 - 健康检查、设备检测
"""
import platform
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import get
from app.shared_executor import inference_scheduler
from core.paths import CONFIG_DIR, CACHE_ROOT, TMP_DIR, OUTPUT_DIR
from core.utils import default_device_for_platform

router = APIRouter()


@router.get("/health")
async def health_check():
    return {"status": "ok", "version": "2.0.0"}


def get_runtime_paths() -> dict[str, Path]:
    return {
        "config": CONFIG_DIR,
        "cache": CACHE_ROOT,
        "tmp": TMP_DIR,
        "output": OUTPUT_DIR,
    }


def _directory_status(path: Path) -> dict:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".ready-", delete=True):
            pass
        return {"path": str(path), "writable": True}
    except OSError as exc:
        return {"path": str(path), "writable": False, "error": str(exc)}


def device_availability(
    device: str,
    torch_module: Any | None = None,
) -> tuple[bool, str]:
    """检查指定计算设备是否可用于推理，并返回不可用原因。"""
    normalized = device.lower()
    if normalized == "cpu":
        return True, ""
    if normalized not in {"cuda", "mps"}:
        return False, f"未知计算设备: {device}"
    if torch_module is None:
        try:
            import torch as torch_module
        except Exception as exc:
            return (
                False,
                f"{normalized.upper()} 不可用：torch 加载失败：{exc}",
            )
    try:
        if normalized == "cuda":
            available = bool(
                hasattr(torch_module, "cuda")
                and torch_module.cuda.is_available()
            )
            return (
                (True, "")
                if available
                else (False, "CUDA 不可用：未检测到可用的 NVIDIA GPU")
            )
        available = bool(
            hasattr(torch_module, "backends")
            and hasattr(torch_module.backends, "mps")
            and torch_module.backends.mps.is_available()
        )
        return (
            (True, "")
            if available
            else (False, "MPS 不可用：需要 Apple Silicon 与可用的 MPS 后端")
        )
    except Exception as exc:
        return False, f"{normalized.upper()} 探测失败：{exc}"


@router.get("/ready")
async def readiness_check():
    paths = {name: _directory_status(path) for name, path in get_runtime_paths().items()}
    available = {name: info["writable"] for name, info in paths.items()}
    default_device = default_device_for_platform()
    configured_device = str(
        get("inpaint.default_device", default_device) or default_device
    ).lower()
    device_available, device_reason = device_availability(configured_device)
    inference_queue = inference_scheduler.snapshot()
    ready = (
        all(available.values())
        and device_available
        and bool(inference_queue["accepting"])
    )
    body = {
        "status": "ready" if ready else "not_ready",
        "paths": paths,
        "platform": platform.system(),
        "device": {
            "configured": configured_device,
            "default": default_device,
            "available": device_available,
            "reason": device_reason,
        },
        "inference_queue": inference_queue,
    }
    return JSONResponse(status_code=200 if ready else 503, content=body)


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


