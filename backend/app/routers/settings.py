"""
设置路由 - 获取和更新应用配置
"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from app.config import get_all, save

router = APIRouter()


class SettingsUpdate(BaseModel):
    device: Optional[str] = None
    server_port: Optional[int] = None
    server_keepalive: Optional[int] = None
    server_startup_timeout: Optional[int] = None
    hf_endpoint: Optional[str] = None
    hf_token: Optional[str] = None
    default_dilation: Optional[int] = None
    disable_nsfw: Optional[bool] = None
    # 内存优化选项（扩散模型）
    low_mem: Optional[bool] = None
    cpu_offload: Optional[bool] = None
    cpu_textencoder: Optional[bool] = None


@router.get("/settings")
async def get_settings():
    """获取全部设置"""
    all_settings = get_all()
    return {
        "device": all_settings.get("inpaint.default_device", "mps"),
        "server_port": all_settings.get("server.port", 51821),
        "server_keepalive": all_settings.get("server.keepalive_seconds", 300),
        "server_startup_timeout": all_settings.get("server.startup_timeout", 1800),
        "hf_endpoint": all_settings.get("network.hf_endpoint", "https://huggingface.co"),
        "hf_token": all_settings.get("network.hf_token", ""),
        "default_dilation": all_settings.get("inpaint.default_dilation", 10),
        "disable_nsfw": all_settings.get("inpaint.disable_nsfw", True),
        "low_mem": all_settings.get("server.low_mem", True),
        "cpu_offload": all_settings.get("server.cpu_offload", False),
        "cpu_textencoder": all_settings.get("server.cpu_textencoder", False),
    }


@router.put("/settings")
async def update_settings(data: SettingsUpdate):
    """更新设置"""
    current = get_all()

    if data.device is not None:
        current["inpaint.default_device"] = data.device
    if data.server_port is not None:
        current["server.port"] = data.server_port
    if data.server_keepalive is not None:
        current["server.keepalive_seconds"] = data.server_keepalive
    if data.server_startup_timeout is not None:
        current["server.startup_timeout"] = data.server_startup_timeout
    if data.hf_endpoint is not None:
        current["network.hf_endpoint"] = data.hf_endpoint
    if data.hf_token is not None:
        current["network.hf_token"] = data.hf_token
    if data.default_dilation is not None:
        current["inpaint.default_dilation"] = data.default_dilation
    if data.disable_nsfw is not None:
        current["inpaint.disable_nsfw"] = data.disable_nsfw
    if data.low_mem is not None:
        current["server.low_mem"] = data.low_mem
    if data.cpu_offload is not None:
        current["server.cpu_offload"] = data.cpu_offload
    if data.cpu_textencoder is not None:
        current["server.cpu_textencoder"] = data.cpu_textencoder

    save(current)
    return {"status": "ok", "message": "设置已保存"}
