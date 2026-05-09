"""
项目级常量 —— 统一管理所有魔术字符串，避免散落各处。

使用方式：
    from core.constants import ModelStatus, DownloadStatus, Provider, IOPaintMode
"""
from __future__ import annotations

from typing import ClassVar, Final


# ── 模型文件检测状态 ──────────────────────────────────────────────────────────

class ModelStatus:
    """模型文件完整性检测状态（对应 model_checker.py 返回的 status 字段）。"""
    OK:        ClassVar[str] = "ok"        # 文件完整，可直接使用
    MISSING:   ClassVar[str] = "missing"   # 文件缺失，需要下载
    PARTIAL:   ClassVar[str] = "partial"   # 部分文件缺失（多文件模型）
    CORRUPTED: ClassVar[str] = "corrupted" # 文件存在但损坏（大小异常或 safetensors 头无效）
    UNKNOWN:   ClassVar[str] = "unknown"   # 无法判断（provider 未知 / 检测异常）


# ── 下载任务状态 ──────────────────────────────────────────────────────────────

class DownloadStatus:
    """下载队列任务状态（对应 DownloadTask.status 字段）。"""
    QUEUED:      ClassVar[str] = "queued"      # 已入队，等待槽位
    DOWNLOADING: ClassVar[str] = "downloading" # 正在下载
    DONE:        ClassVar[str] = "done"        # 下载完成
    ERROR:       ClassVar[str] = "error"       # 下载失败
    CANCELLED:   ClassVar[str] = "cancelled"   # 已取消
    SKIPPED:     ClassVar[str] = "skipped"     # 文件已存在，跳过（旧 SSE 接口专用）


# ── 模型 Provider ─────────────────────────────────────────────────────────────

class Provider:
    """models.yaml 中 provider 字段的合法值。"""
    REMBG:      ClassVar[str] = "rembg"
    IOPAINT:    ClassVar[str] = "IOPaint"
    DIFFUSERS:  ClassVar[str] = "diffusers"
    FACEXLIB:   ClassVar[str] = "facexlib"
    REALESRGAN: ClassVar[str] = "realesrgan"
    HIIMAGE:    ClassVar[str] = "HiImage"


# ── IOPaint 运行模式 ──────────────────────────────────────────────────────────

class IOPaintMode:
    """iopaint_mode 字段的合法值。"""
    CLI:    ClassVar[str] = "cli"    # 每次推理启动独立进程
    SERVER: ClassVar[str] = "server" # 常驻 HTTP server（扩散模型）


# ── 设备标识 ──────────────────────────────────────────────────────────────────

class Device:
    """硬件加速设备标识。"""
    CPU:  ClassVar[str] = "cpu"
    CUDA: ClassVar[str] = "cuda"
    MPS:  ClassVar[str] = "mps"


# ── 后处理方法 ────────────────────────────────────────────────────────────────

class PostprocessMethod:
    """后处理方法标识（对应 background_fixer.py / pipeline.py）。"""
    NONE:        ClassVar[str] = "none"
    POISSON:     ClassVar[str] = "poisson"
    GFPGAN:      ClassVar[str] = "gfpgan"
    LAMA_REFINE: ClassVar[str] = "lama_refine"


# ── 配置键 ────────────────────────────────────────────────────────────────────

class ConfigKey:
    """config/settings.json 中的配置键，避免字符串散落各处。"""
    SERVER_PORT:             ClassVar[str] = "server.port"
    SERVER_KEEPALIVE:        ClassVar[str] = "server.keepalive_seconds"
    INPAINT_DEVICE:          ClassVar[str] = "inpaint.default_device"
    INPAINT_IOPAINT_PATH:    ClassVar[str] = "inpaint.iopaint_path"
    NETWORK_HF_ENDPOINT:     ClassVar[str] = "network.hf_endpoint"
    NETWORK_HF_TOKEN:        ClassVar[str] = "network.hf_token"
    NETWORK_GITHUB_MIRROR:   ClassVar[str] = "network.github_mirror"
    DOWNLOAD_MAX_CONCURRENT: ClassVar[str] = "download.max_concurrent"
