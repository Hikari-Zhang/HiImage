"""
路径管理器 — 统一管理项目中的所有路径

所有路径配置集中在此文件，便于维护和自定义。
环境变量优先级 > 默认值。

用法：
    from core.paths import HF_HOME, MODELS_DIR, resolve_model_path
    print(HF_HOME)  # ~/.cache/huggingface
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ──────────────────────────────────────────────────────────────
# 项目根目录
# ──────────────────────────────────────────────────────────────
# backend/ 的上级目录（即 PROJECT_ROOT）；容器可用环境变量明确指定。
_SOURCE_ROOT = Path(__file__).resolve().parent.parent.parent
PROJECT_ROOT = Path(os.environ.get("HIIMAGE_PROJECT_ROOT", str(_SOURCE_ROOT))).expanduser()


@dataclass(frozen=True)
class RuntimePaths:
    """运行时目录集合。所有目录都可通过环境变量覆盖。"""

    project_root: Path
    config_dir: Path
    cache_dir: Path
    tmp_dir: Path
    output_dir: Path
    models_dir: Path


def paths_from_env(env: Optional[dict[str, str]] = None) -> RuntimePaths:
    """根据环境变量解析目录；传入 env 便于测试且不需要 reload 模块。"""
    values = os.environ if env is None else env
    project_root = Path(values.get("HIIMAGE_PROJECT_ROOT", str(PROJECT_ROOT))).expanduser()
    return RuntimePaths(
        project_root=project_root,
        config_dir=Path(values.get("HIIMAGE_CONFIG_DIR", str(project_root / "config"))).expanduser(),
        cache_dir=Path(values.get("HIIMAGE_CACHE", str(Path.home() / ".cache"))).expanduser(),
        tmp_dir=Path(values.get("HIIMAGE_TMP_DIR", str(project_root / "tmp"))).expanduser(),
        output_dir=Path(values.get("HIIMAGE_OUTPUT_DIR", str(project_root / "output"))).expanduser(),
        models_dir=Path(values.get("HIIMAGE_MODELS_DIR", str(project_root / "models"))).expanduser(),
    )


def cache_dirs_from_env(env: Optional[dict[str, str]] = None) -> dict[str, Path]:
    """解析模型缓存目录；专用变量优先于 HIIMAGE_CACHE。"""
    values = os.environ if env is None else env
    cache = Path(values.get("HIIMAGE_CACHE", str(Path.home() / ".cache"))).expanduser()
    u2net_default = cache / "u2net" if "HIIMAGE_CACHE" in values else Path.home() / ".u2net"
    defaults = {
        "HF_HOME": cache / "huggingface",
        "TORCH_HOME": cache / "torch",
        "GFPGAN_HOME": cache / "gfpgan",
        "REALESRGAN_HOME": cache / "realesrgan",
        "RESTORMER_HOME": cache / "restormer",
        "U2NET_HOME": u2net_default,
    }
    return {
        name: Path(values.get(name, str(default))).expanduser()
        for name, default in defaults.items()
    }


_RUNTIME_PATHS = paths_from_env()
CONFIG_DIR = _RUNTIME_PATHS.config_dir
CONFIG_PATH = CONFIG_DIR / "settings.json"
TMP_DIR = _RUNTIME_PATHS.tmp_dir
OUTPUT_DIR = _RUNTIME_PATHS.output_dir

# ──────────────────────────────────────────────────────────────
# 默认缓存根目录：~/.cache/
# 可通过环境变量 HIIMAGE_CACHE 自定义（默认值已从 hiimage/ 移除）
# ──────────────────────────────────────────────────────────────
_DEFAULT_CACHE_ROOT = Path.home() / ".cache"
CACHE_ROOT = _RUNTIME_PATHS.cache_dir

# ──────────────────────────────────────────────────────────────
# 各类型模型缓存目录
# ──────────────────────────────────────────────────────────────

# HuggingFace 模型缓存
# 默认使用 huggingface_hub 官方路径 ~/.cache/huggingface
# 可通过环境变量 HF_HOME 覆盖（与 huggingface_hub 保持一致）
HF_HOME = cache_dirs_from_env()["HF_HOME"]

# Torch 模型缓存
# 可通过环境变量 TORCH_HOME 覆盖（与 torch.hub 保持一致）
TORCH_HOME = cache_dirs_from_env()["TORCH_HOME"]

# GFPGAN 权重缓存
# 可通过环境变量 GFPGAN_HOME 覆盖
GFPGAN_HOME = cache_dirs_from_env()["GFPGAN_HOME"]

# Real-ESRGAN 权重缓存
# 可通过环境变量 REALESRGAN_HOME 覆盖
REALESRGAN_HOME = cache_dirs_from_env()["REALESRGAN_HOME"]

# Restormer 权重缓存
# 可通过环境变量 RESTORMER_HOME 覆盖
RESTORMER_HOME = cache_dirs_from_env()["RESTORMER_HOME"]

# rembg / U2NET 模型缓存
# 可通过环境变量 U2NET_HOME 覆盖（与 rembg 保持一致）
U2NET_HOME = cache_dirs_from_env()["U2NET_HOME"]

# 项目 models/ 目录（向后兼容旧代码）
# ⚠️  已废弃：新代码请使用 MODELS_CACHE_DIR（~/.cache/models）；此变量仅作向后兼容保留。
MODELS_DIR = _RUNTIME_PATHS.models_dir

# 模型缓存目录（默认下载位置）：~/.cache/hiimage/models/
# 可通过环境变量 HIIMAGE_CACHE 自定义（会自动附加 /models）
MODELS_CACHE_DIR = CACHE_ROOT / "models"


def ensure_runtime_dirs() -> RuntimePaths:
    """创建配置、缓存、临时和输出目录，并返回目录集合。"""
    for path in (CONFIG_DIR, CACHE_ROOT, TMP_DIR, OUTPUT_DIR):
        path.mkdir(parents=True, exist_ok=True)
    return _RUNTIME_PATHS

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

    检查顺序：
      1. 标准 hub 缓存（新下载位置）：通过 snapshot_download(local_files_only=True) 获取 snapshot 路径
      2. manual/ 扁平目录（旧下载位置，向后兼容）
      3. 均未找到：返回原始 repo_id，由 transformers/diffusers 自行解析

    用法：
        from core.paths import resolve_hf_model_path
        local_path = resolve_hf_model_path("timbrooks/instruct-pix2pix")
    """
    # 1. 标准 hub 缓存（新路径）
    hub_dir = HF_HOME / "hub"
    if hub_dir.exists():
        try:
            from huggingface_hub import snapshot_download
            path = snapshot_download(
                repo_id=repo_id,
                local_files_only=True,
                cache_dir=str(hub_dir),
            )
            if path:
                return path
        except Exception:
            pass

    # 2. manual/ 扁平目录（旧路径，向后兼容）
    manual_dir = get_hf_manual_dir() / repo_id.replace("/", "--")
    if manual_dir.exists() and manual_dir.is_dir():
        return str(manual_dir)

    # 3. 未找到：返回 repo_id 让 diffusers 自行解析（可能会触发在线下载）
    return repo_id


def resolve_model_cache_path(cfg: dict) -> Path:
    """
    统一解析模型缓存路径（下载/加载/检查均使用此函数）。

    根据 provider 类型，自动定位到正确的缓存子目录：
      - realesrgan  → REALESRGAN_HOME / weight_filename  （~/.cache/realesrgan/）
      - restormer   → RESTORMER_HOME / weight_filename   （~/.cache/restormer/）
      - gfpgan      → GFPGAN_HOME / weight_filename      （~/.cache/gfpgan/）
      - 其他        → resolve_model_path(local_path)     # 向后兼容

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
        return REALESRGAN_HOME / weight_filename
    elif provider == "restormer":
        return RESTORMER_HOME / weight_filename
    elif provider == "gfpgan":
        return GFPGAN_HOME / weight_filename
    else:
        # 向后兼容：使用原有的 resolve_model_path
        local_path = cfg.get("local_path", "")
        return resolve_model_path(local_path)


def detect_iopaint_path() -> str:
    """
    从当前 Python 环境的 Scripts/bin 目录查找 iopaint 可执行文件。

    检查顺序（Windows 兼容）：
      1. <python_dir>/iopaint.exe  （Windows）
      2. <python_dir>/iopaint      （macOS / Linux）
      3. 均未找到时返回 "iopaint"，依赖系统 PATH

    用法：
        from core.paths import detect_iopaint_path
        iopaint = detect_iopaint_path()
    """
    python_dir = Path(sys.executable).parent
    for candidate in (python_dir / "iopaint.exe", python_dir / "iopaint"):
        if candidate.exists():
            return str(candidate)
    return "iopaint"


# ──────────────────────────────────────────────────────────────
# 向后兼容：保留旧的环境变量设置函数
# ──────────────────────────────────────────────────────────────

def apply_default_env_vars() -> None:
    """
    为依赖环境变量的库（huggingface_hub, torch, gfpgan, rembg 等）设置默认环境变量。

    注意：此函数仅设置*未定义*的环境变量，不会覆盖用户已设置的值。
    在 app/main.py 启动时调用。

    当前设置的变量：
      - HF_HOME         : HuggingFace 缓存目录
      - TORCH_HOME      : Torch 缓存目录
      - GFPGAN_HOME     : GFPGAN 权重缓存目录
      - REALESRGAN_HOME : Real-ESRGAN 权重缓存目录
      - RESTORMER_HOME  : Restormer 权重缓存目录
      - U2NET_HOME      : rembg / U2NET 模型缓存目录
    """
    _set_default("HF_HOME",         str(HF_HOME))
    _set_default("TORCH_HOME",      str(TORCH_HOME))
    _set_default("GFPGAN_HOME",     str(GFPGAN_HOME))
    _set_default("REALESRGAN_HOME", str(REALESRGAN_HOME))
    _set_default("RESTORMER_HOME",  str(RESTORMER_HOME))
    _set_default("U2NET_HOME",      str(U2NET_HOME))


def _set_default(var_name: str, default_value: str) -> None:
    """如果环境变量未设置，则设置默认值"""
    if var_name not in os.environ:
        os.environ[var_name] = default_value
