"""功能不可用时的统一 HTTP 响应。"""
import asyncio

from fastapi.responses import JSONResponse
from core.capabilities import FeatureUnavailable, require_mode, require_model
from core.model_registry import get_model


def unavailable_response(mode: str, model_id: str | None = None):
    try:
        if model_id is None:
            require_mode(mode)
        else:
            require_model(mode, model_id)
    except FeatureUnavailable as exc:
        return JSONResponse(status_code=503, content={
            "code": "FEATURE_UNAVAILABLE", "message": str(exc), "detail": str(exc), "retryable": False,
        })
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"message": str(exc), "detail": str(exc)})
    return None


async def redraw_response(model_id, device, redraw):
    if get_model(model_id)["pipeline"] != "flux_fill":
        if redraw is not None:
            return JSONResponse(status_code=422, content={
                "code": "INVALID_REDRAW_OPTIONS", "message": "当前模型不支持局部重绘参数",
            })
        return None

    def cuda_available():
        import torch
        return torch.cuda.is_available()

    if device != "cuda" or not await asyncio.to_thread(cuda_available):
        return JSONResponse(status_code=422, content={
            "code": "REDRAW_CUDA_REQUIRED", "message": "FLUX Fill 局部重绘需要可用的 CUDA 设备",
        })
    return None
