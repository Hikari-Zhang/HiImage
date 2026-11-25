"""局部重绘请求可选参数，执行器继续校验模型配置中的取值范围。"""
from pydantic import BaseModel, ConfigDict, Field

from app.config import get
from core.model_registry import get_model


class RedrawOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str | None = Field(default=None, max_length=1000)
    seed: int | None = Field(default=None, strict=True, ge=0, le=2147483647)
    num_inference_steps: int | None = Field(default=None, strict=True, ge=1, le=50)
    guidance_scale: float | None = Field(default=None, strict=True, ge=1, le=30)


def resolve_watermark_options(model_id: str, dilation: int | None, redraw: RedrawOptions | None) -> dict:
    """合并去水印模型默认值与本次请求的显式参数。"""
    defaults = get_model(model_id).get("watermark_defaults", {})
    if dilation is None and model_id == "wm_lama":
        dilation = get("inpaint.lama_dilation")
    if dilation is None:
        dilation = defaults["dilation"] if "dilation" in defaults else get("inpaint.default_dilation", 10)
    parameters = defaults.get("redraw", {}).copy()
    if redraw is not None:
        parameters.update(redraw.model_dump(exclude_none=True))
    return {"dilation": dilation, "redraw": parameters or None}
