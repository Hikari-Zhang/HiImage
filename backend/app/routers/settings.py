"""
设置路由 - 获取和更新应用配置
"""
import os
from collections.abc import MutableMapping
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.config import get_all, save
from core.utils import default_device_for_platform

router = APIRouter()


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device: Optional[str] = None
    hf_endpoint: Optional[str] = None
    hf_token: Optional[str] = None
    clear_hf_token: bool = False
    github_mirror: Optional[str] = None
    default_dilation: Optional[int] = None
    disable_nsfw: Optional[bool] = None
    # 内存优化选项（扩散模型）
    # 下载配置
    download_max_concurrent: Optional[int] = None
    # 超分辨率 tile 设置（0 = 自动计算）
    upscale_tile: Optional[int] = None


def sync_hf_environment(
    data: SettingsUpdate,
    environ: MutableMapping[str, str] | None = None,
) -> None:
    """配置保存成功后，将本次 HF 变更同步到运行时环境。"""
    target = os.environ if environ is None else environ
    if data.hf_endpoint is not None:
        target["HF_ENDPOINT"] = data.hf_endpoint
    if data.clear_hf_token:
        target.pop("HF_TOKEN", None)
    elif data.hf_token is not None and data.hf_token.strip():
        target["HF_TOKEN"] = data.hf_token.strip()


@router.get("/settings")
async def get_settings():
    """获取全部设置"""
    all_settings = get_all()
    return {
        "device": all_settings.get("inpaint.default_device", default_device_for_platform()),
        "hf_endpoint": all_settings.get("network.hf_endpoint", "https://huggingface.com"),
        "hf_token_configured": bool(all_settings.get("network.hf_token", "")),
        "github_mirror": all_settings.get("network.github_mirror", ""),
        "default_dilation": all_settings.get("inpaint.default_dilation", 10),
        "disable_nsfw": all_settings.get("inpaint.disable_nsfw", True),
        "download_max_concurrent": all_settings.get("download.max_concurrent", 3),
        # 超分辨率 tile 设置（0 = 自动计算）
        "upscale_tile": all_settings.get("upscale.tile", 0),
    }


@router.put("/settings")
async def update_settings(data: SettingsUpdate):
    """更新设置"""
    current = {key: value for key, value in get_all().items() if not key.startswith("server.") and key != "inpaint.iopaint_path"}

    if data.device is not None:
        current["inpaint.default_device"] = data.device
    if data.hf_endpoint is not None:
        current["network.hf_endpoint"] = data.hf_endpoint
    if data.clear_hf_token:
        current["network.hf_token"] = ""
    elif data.hf_token is not None and data.hf_token.strip():
        current["network.hf_token"] = data.hf_token.strip()
    if data.github_mirror is not None:
        current["network.github_mirror"] = data.github_mirror
    if data.default_dilation is not None:
        current["inpaint.default_dilation"] = data.default_dilation
    if data.disable_nsfw is not None:
        current["inpaint.disable_nsfw"] = data.disable_nsfw
    if data.download_max_concurrent is not None:
        current["download.max_concurrent"] = max(1, min(10, data.download_max_concurrent))
    # 超分辨率 tile 设置（0 = 自动计算；>0 = 固定 tile 大小）
    if data.upscale_tile is not None:
        current["upscale.tile"] = max(0, data.upscale_tile)

    save(current)
    sync_hf_environment(data)
    return {"status": "ok", "message": "设置已保存"}
