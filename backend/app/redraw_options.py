"""局部重绘请求可选参数，执行器继续校验模型配置中的取值范围。"""
from pydantic import BaseModel, ConfigDict, Field


class RedrawOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str | None = Field(default=None, max_length=1000)
    seed: int | None = Field(default=None, strict=True, ge=0, le=2147483647)
    num_inference_steps: int | None = Field(default=None, strict=True, ge=1, le=50)
    guidance_scale: float | None = Field(default=None, strict=True, ge=1, le=30)
