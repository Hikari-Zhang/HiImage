"""
模型注册表 —— 运行时从 models.yaml 懒加载

所有模型与模式配置均在 core/models.yaml 中维护。
本文件只负责：
  1. 读取并解析 YAML（首次访问时才执行，避免导入时 I/O）
  2. 展开 mode_groups.models（按 tags 自动关联）
  3. 提供对外接口：MODELS / MODE_GROUPS / MODEL_BY_ID / MODE_BY_ID
  4. 提供工具函数：get_model / get_mode / get_models_for_mode

新增模型：只需编辑 core/models.yaml，本文件无需改动。
"""
from __future__ import annotations

import threading
import yaml
from pathlib import Path
from typing import Any

# YAML 文件与本模块同目录
_YAML_PATH = Path(__file__).parent / "models.yaml"


def _load() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """读取 models.yaml，返回 (models, mode_groups)。"""
    try:
        with open(_YAML_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        raise RuntimeError(f"models.yaml 不存在: {_YAML_PATH}")
    except yaml.YAMLError as e:
        raise RuntimeError(f"models.yaml 格式错误: {e}")

    raw_models: list[dict] = data.get("models", [])
    raw_modes: list[dict] = data.get("mode_groups", [])

    # 为每个模式自动填充 models 字段（根据 tags 关联）
    for mode in raw_modes:
        mode_id = mode["id"]
        mode["models"] = [m["id"] for m in raw_models if mode_id in m.get("tags", [])]

    return raw_models, raw_modes


# ── 懒加载缓存（首次调用 _ensure_loaded() 后才有非空值）─────────────────────────────

_lock = threading.Lock()
_loaded: bool = False

# 下划线前缀为内部名；公开名称（MODELS / MODEL_BY_ID 等）通过 __getattr__ 暴露，
# 确保外部 `from core.model_registry import MODELS` 也能触发懒加载。
_MODELS:      list[dict[str, Any]] = []
_MODE_GROUPS: list[dict[str, Any]] = []
_MODEL_BY_ID: dict[str, dict]      = {}
_MODE_BY_ID:  dict[str, dict]      = {}


def _ensure_loaded() -> None:
    """YAML 懒加载入口（双检锁，线程安全）。首次调用时执行磁盘 I/O。"""
    global _loaded, _MODELS, _MODE_GROUPS, _MODEL_BY_ID, _MODE_BY_ID
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        models, modes = _load()
        _MODEL_BY_ID = {m["id"]: m for m in models}
        _MODE_BY_ID  = {g["id"]: g for g in modes}
        _MODELS      = models
        _MODE_GROUPS = modes
        _loaded      = True


_PUBLIC_ALIASES = {
    "MODELS":      "_MODELS",
    "MODE_GROUPS": "_MODE_GROUPS",
    "MODEL_BY_ID": "_MODEL_BY_ID",
    "MODE_BY_ID":  "_MODE_BY_ID",
}


def __getattr__(name: str):
    """懒加载入口：外部 `from core.model_registry import MODELS` 触发此函数。"""
    if name in _PUBLIC_ALIASES:
        _ensure_loaded()
        return globals()[_PUBLIC_ALIASES[name]]
    raise AttributeError(f"module 'core.model_registry' has no attribute {name!r}")


# ── 工具函数 ────────────────────────────────────────────────────────────────

def get_default_model(mode_id: str) -> str:
    """
    返回指定模式的默认模型 ID。

    从 models.yaml 的 mode_groups 中读取 default_model 字段。
    用于替代代码中的硬编码默认值。

    :param mode_id: 模式 ID（如 "watermark_removal"）
    :return: 默认模型 ID（如 "wm_lama"）
    :raises KeyError: 模式不存在或未配置 default_model
    """
    mode = get_mode(mode_id)
    default = mode.get("default_model")
    if not default:
        raise KeyError(f"模式 {mode_id!r} 未配置 default_model")
    return default


def get_models_for_mode(mode_id: str) -> list[dict]:
    """返回指定模式下的所有模型（保留 YAML 中的原始顺序）。"""
    _ensure_loaded()
    return [m for m in _MODELS if mode_id in m.get("tags", [])]


def get_model(model_id: str) -> dict:
    """按 ID 获取模型配置；不存在时抛出 KeyError。"""
    _ensure_loaded()
    if model_id not in _MODEL_BY_ID:
        raise KeyError(f"未知模型 ID: {model_id!r}，可选: {list(_MODEL_BY_ID)}")
    return _MODEL_BY_ID[model_id]


def get_mode(mode_id: str) -> dict:
    """按 ID 获取模式配置；不存在时抛出 KeyError。"""
    _ensure_loaded()
    if mode_id not in _MODE_BY_ID:
        raise KeyError(f"未知模式 ID: {mode_id!r}，可选: {list(_MODE_BY_ID)}")
    return _MODE_BY_ID[mode_id]


def reload() -> None:
    """热重载配置（开发调试用；生产环境重启进程即可）。"""
    global _loaded, _MODELS, _MODE_GROUPS, _MODEL_BY_ID, _MODE_BY_ID
    # IO 在锁外完成，避免长时间持有锁
    models, modes = _load()
    model_by_id = {m["id"]: m for m in models}
    mode_by_id  = {g["id"]: g for g in modes}
    # 原子替换全局引用，防止多线程并发读到半更新状态
    with _lock:
        _MODELS      = models
        _MODE_GROUPS = modes
        _MODEL_BY_ID = model_by_id
        _MODE_BY_ID  = mode_by_id
        _loaded      = True
