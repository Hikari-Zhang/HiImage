"""
路径管理器 — 统一管理项目中的所有路径

所有路径配置集中在此文件，便于维护和自定义。
模型库按设置顺序查找，默认位于 ~/.cache/HiImage，旧版路径用于兼容。

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
    xdg_cache = Path(values.get("XDG_CACHE_HOME") or str(cache)).expanduser()
    hf_default = cache if "HIIMAGE_CACHE" in values else xdg_cache
    defaults = {
        "HF_HOME": hf_default / "huggingface",
        "TORCH_HOME": hf_default / "torch",
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
# 旧安装位置。模型读写请通过下方的路径解析函数。
MODELS_DIR = _RUNTIME_PATHS.models_dir

# 旧版缓存路径，保留给兼容调用方。
MODELS_CACHE_DIR = CACHE_ROOT / "models"


def ensure_runtime_dirs() -> RuntimePaths:
    """创建配置、缓存、临时和输出目录，并返回目录集合。"""
    for path in (CONFIG_DIR, CACHE_ROOT, TMP_DIR, OUTPUT_DIR):
        path.mkdir(parents=True, exist_ok=True)
    return _RUNTIME_PATHS

# ──────────────────────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────────────────────

def default_model_directory() -> Path:
    return CACHE_ROOT / "HiImage"


def model_storage_dirs() -> tuple[Path, ...]:
    from app.config import get

    configured = (Path(value) for value in get("storage.model_directories", []))
    return tuple(dict.fromkeys((*configured, default_model_directory())))


def storage_locations(subdirectory: str, legacy: Path) -> tuple[Path, ...]:
    """按配置顺序查找，然后查找默认模型库及旧版安装路径。"""
    locations = (root / subdirectory for root in model_storage_dirs())
    return tuple(dict.fromkeys((*locations, legacy)))


def hf_cache_locations() -> tuple[tuple[Path, Path | None], ...]:
    """每个库先查 Hub 快照，再查手动安装，最后兼容原来的 HF 环境变量。"""
    locations = [(root / "huggingface/hub", root / "huggingface/manual") for root in model_storage_dirs()]
    value = os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE")
    if value:
        locations.append((Path(os.path.expandvars(value)).expanduser(), None))
    locations.append((HF_HOME / "hub", HF_HOME / "manual"))
    return tuple(dict.fromkeys(locations))


def get_hf_hub_dir() -> Path:
    return model_storage_dirs()[0] / "huggingface/hub"


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


def find_hf_snapshot(repo_id: str) -> Optional[Path]:
    """返回加载器实际使用的 main 快照或兼容的手动缓存。"""
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError

    for hub_dir, manual in hf_cache_locations():
        try:
            path = snapshot_download(
                repo_id=repo_id,
                local_files_only=True,
                cache_dir=str(hub_dir),
            )
            if Path(path).is_dir():
                return Path(path)
        except LocalEntryNotFoundError:
            pass
        if manual is not None:
            directory = manual / repo_id.replace("/", "--")
            if directory.is_dir():
                return directory
    return None


def resolve_hf_model_path(repo_id: str) -> str:
    path = find_hf_snapshot(repo_id)
    return str(path) if path is not None else repo_id


def model_cache_root(cfg: dict) -> Optional[Path]:
    """返回按模型类型管理的权重目录。"""
    if cfg.get("executor") == "gfpgan":
        return GFPGAN_HOME
    return {
        "realesrgan": REALESRGAN_HOME,
        "restormer": RESTORMER_HOME,
        "gfpgan": GFPGAN_HOME,
    }.get(cfg.get("provider"))


def local_model_locations(cfg: dict) -> tuple[Path, ...]:
    local_path = cfg.get("local_path", "")
    filename = cfg.get("weight_filename") or Path(local_path).name
    root = model_cache_root(cfg)
    default = root / filename if root is not None else resolve_model_path(local_path)
    provider = "gfpgan" if cfg.get("executor") == "gfpgan" else cfg.get("provider")
    if not provider:
        return (default,)
    return storage_locations(str(Path(provider) / filename), default)


def resolve_model_cache_path(cfg: dict) -> Path:
    """本次下载的目标文件。"""
    return local_model_locations(cfg)[0]


def legacy_model_cache_path(cfg: dict) -> Optional[tuple[Path, Path]]:
    """返回旧版 HiImage 权重路径和它所属的缓存目录。"""
    weight = cfg.get("weight_filename") or Path(cfg.get("local_path", "")).name
    provider = "gfpgan" if cfg.get("executor") == "gfpgan" else cfg.get("provider")
    cache = Path.home() / ".cache" / "hiimage"
    roots = {"realesrgan": cache / "models" / "realesrgan",
             "restormer": cache / "models" / "restormer", "gfpgan": cache / "gfpgan"}
    if weight and (root := roots.get(provider)) is not None:
        return root / weight, root
    return None


def find_local_model_path(cfg: dict) -> Path:
    locations = local_model_locations(cfg)
    legacy = legacy_model_cache_path(cfg)
    if legacy:
        locations = (*locations, legacy[0])
    return next((path for path in locations if path.is_file()), locations[0])


def rembg_locations(cfg: dict) -> tuple[tuple[Path, Path], ...]:
    session = cfg.get("rembg_session_name", cfg.get("rembg_model_name", cfg["id"]))
    filename = cfg.get("onnx_filename", f"{session}.onnx")
    return tuple((root / filename, root) for root in storage_locations("rembg", U2NET_HOME))


def rembg_model_path(cfg: dict, *, installed: bool = False) -> Path:
    locations = rembg_locations(cfg)
    if installed:
        for path, _ in locations:
            if path.is_file():
                return path
    return locations[0][0]


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
