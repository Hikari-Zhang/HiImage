"""
路径管理器 — 统一管理项目中的所有路径

所有路径配置集中在此文件，便于维护和自定义。
环境变量优先级 > 默认值。

用法：
    from core.paths import HF_HOME, MODELS_DIR, resolve_model_path
    print(HF_HOME)  # ~/.cache/hiimage/huggingface
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# ──────────────────────────────────────────────────────────────
# 项目根目录
# ──────────────────────────────────────────────────────────────
# backend/ 的上级目录（即 PROJECT_ROOT）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ──────────────────────────────────────────────────────────────
# 默认缓存根目录：~/.cache/hiimage/
# 可通过环境变量 HIIMAGE_CACHE 自定义
# ──────────────────────────────────────────────────────────────
_DEFAULT_CACHE_ROOT = Path.home() / ".cache" / "hiimage"
CACHE_ROOT = Path(os.environ.get("HIIMAGE_CACHE", str(_DEFAULT_CACHE_ROOT)))

# ──────────────────────────────────────────────────────────────
# 各类型模型缓存目录
# ──────────────────────────────────────────────────────────────

# HuggingFace 模型缓存
# 默认使用 huggingface_hub 官方路径 ~/.cache/huggingface
# 可通过环境变量 HF_HOME 覆盖（与 huggingface_hub 保持一致）
HF_HOME = Path(os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface")))

# Torch 模型缓存
# 可通过环境变量 TORCH_HOME 覆盖（与 torch.hub 保持一致）
TORCH_HOME = Path(os.environ.get("TORCH_HOME", str(CACHE_ROOT / "torch")))

# GFPGAN 权重缓存
# 可通过环境变量 GFPGAN_HOME 覆盖
GFPGAN_HOME = Path(os.environ.get("GFPGAN_HOME", str(CACHE_ROOT / "gfpgan")))

# rembg / U2NET 模型缓存
# 可通过环境变量 U2NET_HOME 覆盖（与 rembg 保持一致）
U2NET_HOME = Path(os.environ.get("U2NET_HOME", str(Path.home() / ".u2net")))

# 项目 models/ 目录（向后兼容旧代码）
MODELS_DIR = PROJECT_ROOT / "models"

# 模型缓存目录（默认下载位置）：~/.cache/hiimage/models/
# 可通过环境变量 HIIMAGE_CACHE 自定义（会自动附加 /models）
MODELS_CACHE_DIR = CACHE_ROOT / "models"

# ──────────────────────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────────────────────

def get_hf_hub_dir() -> Path:
    """获取 HuggingFace hub 缓存目录（hub/ 子目录）"""
    return HF_HOME / "hub"


def get_hf_manual_dir() -> Path:
    """获取 HuggingFace 手动下载目录（manual/ 子目录）"""
    return HF_HOME / "manual"


def ensure_dir(path: Path) -> Path:
    """确保目录存在，返回路径本身"""
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_model_path(local_path: str, project_root: Optional[Path] = None) -> Path:
    """
    解析模型路径：
      - 包含 ~ 的路径 → 展开为用户目录
      - 绝对路径     → 直接使用
      - 相对路径     → 相对于 PROJECT_ROOT（可自定义）

    用法：
        path = resolve_model_path("~/.cache/gfpgan/GFPGANv1.4.pth")
        path = resolve_model_path("/absolute/path/to/model.pth")
        path = resolve_model_path("models/gfpgan/GFPGANv1.4.pth")  # → PROJECT_ROOT/models/...
    """
    if project_root is None:
        project_root = PROJECT_ROOT
    p = Path(local_path).expanduser()
    if p.is_absolute():
        return p
    return project_root / local_path


def resolve_hf_model_path(repo_id: str) -> str:
    """
    解析 HF 模型加载路径。

    优先使用 manual/ 目录（HiImage 下载路径），
    找不到时返回原始 repo_id，由 transformers/diffusers 自行解析标准 HF 缓存。

    用法：
        from core.paths import resolve_hf_model_path
        local_path = resolve_hf_model_path("timbrooks/instruct-pix2pix")
    """
    manual_dir = get_hf_manual_dir() / repo_id.replace("/", "--")
    if manual_dir.exists() and manual_dir.is_dir():
        return str(manual_dir)
    return repo_id


def resolve_model_cache_path(cfg: dict) -> Path:
    """
    统一解析模型缓存路径（下载/加载/检查均使用此函数）。

    根据 provider 类型，自动定位到正确的缓存子目录：
      - realesrgan  → MODELS_CACHE_DIR / "realesrgan" / weight_filename
      - restormer    → MODELS_CACHE_DIR / "restormer" / weight_filename
      - gfpgan       → GFPGAN_HOME / weight_filename
      - 其他         → resolve_model_path(local_path)  # 向后兼容

    优先级：
      1. cfg["weight_filename"]  # 文件名
      2. cfg["local_path"] 的最后一段（basename）

    用法：
        from core.paths import resolve_model_cache_path
        path = resolve_model_cache_path(cfg)
    """
    provider = cfg.get("provider", "")
    weight_filename = cfg.get("weight_filename", "")

    # 如果没有 weight_filename，尝试从 local_path 提取
    if not weight_filename:
        local_path = cfg.get("local_path", "")
        weight_filename = os.path.basename(local_path)

    # 根据 provider 定位到正确的缓存目录
    if provider == "realesrgan":
        return MODELS_CACHE_DIR / "realesrgan" / weight_filename
    elif provider == "restormer":
        return MODELS_CACHE_DIR / "restormer" / weight_filename
    elif provider == "gfpgan":
        return GFPGAN_HOME / weight_filename
    else:
        # 向后兼容：使用原有的 resolve_model_path
        local_path = cfg.get("local_path", "")
        return resolve_model_path(local_path)


# ──────────────────────────────────────────────────────────────
# 向后兼容：保留旧的环境变量设置函数
# ──────────────────────────────────────────────────────────────

def apply_default_env_vars() -> None:
    """
    为依赖环境变量的库（huggingface_hub, torch 等）设置默认环境变量。

    注意：此函数仅设置*未定义*的环境变量，不会覆盖用户已设置的值。
    在 app/main.py 启动时调用。

    当前设置的变量：
      - HF_HOME       : HuggingFace 缓存目录
      - TORCH_HOME    : Torch 缓存目录
    """
    _set_default("HF_HOME", str(HF_HOME))
    _set_default("TORCH_HOME", str(TORCH_HOME))


def _set_default(var_name: str, default_value: str) -> None:
    """如果环境变量未设置，则设置默认值"""
    if var_name not in os.environ:
        os.environ[var_name] = default_value
