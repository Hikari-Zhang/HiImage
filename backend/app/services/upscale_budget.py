"""独立超分与 Pipeline 共用输出倍率和预算规则。"""
import numpy as np
from fastapi import HTTPException

from app.services.image_codec import enforce_rgba_budget


def output_scale(model_name: str, outscale: int | None = None) -> float:
    if outscale is not None:
        return float(outscale)
    from core.model_registry import get_model

    try:
        model = get_model(model_name)
        return float(model.get("outscale", model.get("scale", 4)))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, "无法解析超分模型的输出倍率") from exc


def enforce_upscale_budget(
    image: np.ndarray,
    model_name: str,
    outscale: int | None = None,
    *,
    max_mb: int | None = None,
) -> int:
    return enforce_rgba_budget(image.shape[1], image.shape[0], output_scale(model_name, outscale), max_mb=max_mb)
